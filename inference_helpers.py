def generate_inference_code(onnx_file):
    import ROOT

    SOFIE = ROOT.TMVA.Experimental.SOFIE

    import numpy as np

    """Parse the ONNX file and return self-contained, AD-friendly C++ as a string."""
    model = SOFIE.RModelParser_ONNX().Parse(onnx_file, False)

    model.SetOptimizationLevel(SOFIE.OptimizationLevel.kBasic)  # no memory reuse -> AD-safe
    model.Generate(SOFIE.Options.kNoWeightFile)  # embed weights in the code

    # Flat number of input values, from the model's input tensor shape.
    in_name = model.GetInputTensorNames()[0]
    n_in = int(np.prod([int(d) for d in model.GetTensorShape(in_name)]))

    code = ROOT.std.stringstream()
    model.PrintGenerated(code)
    return code.str(), n_in


def jit_code_and_gradient(model_name, generated_code, N_IN):
    import ROOT
    import numpy as np

    SOFIE = ROOT.TMVA.Experimental.SOFIE

    # JIT-compile the generated inference code together with the Clad derivatives
    # of the SOFIE primitives (Gemm_Call_pullback, etc.).
    if not ROOT.gInterpreter.Declare('#include <Math/CladDerivator.h>\n' + generated_code):
        raise RuntimeError("Failed to JIT-compile the generated SOFIE code")

    ns = "TMVA_SOFIE_" + model_name  # namespace of the generated code

    # Run one forward pass to discover the flat output size.
    session0 = getattr(ROOT, ns).Session()
    N_OUT = session0.infer(np.zeros(N_IN, dtype=np.float32)).size()

    # Define the scalar neural function we want to differentiate and let Clad
    # build its gradient. We expose two tiny Python-facing helpers, value() and
    # gradient(), that drive the forward and reverse passes.
    ROOT.gInterpreter.Declare(f'''
    namespace Demo_{model_name} {{

    using Session = {ns}::Session;

    // Scalar function of the network: f(input) = sum of all outputs.
    // Any differentiable scalar reduction of the output would work here.
    float f(Session const &session, float const *input) {{
       float out[{N_OUT}]{{}};
       {ns}::doInfer(session, input, out);
       float s = 0.f;
       for (std::size_t i = 0; i < std::size(out); ++i) s += out[i];
       return s;
    }}

    // Differentiating this outer wrapper makes Clad emit the lower-level
    // reverse-mode "pullback" f_pullback, which we can call directly.
    float f_outer(Session const &session, float const *input) {{ return f(session, input); }}

    }}
    ''')

    # clad::gradient(f_outer, "input") implicitly generates f_pullback with signature
    #   f_pullback(session, input, d_y, &d_session, d_input)
    ROOT.gInterpreter.ProcessLine(f'clad::gradient(Demo_{model_name}::f_outer, "input");')

    ROOT.gInterpreter.Declare(f'''
    namespace Demo_{model_name} {{

    Session session;  // forward pass (weights come from the embedded data)

    float value(float const *input) {{ return f(session, input); }}

    // grad[i] = d f / d input[i]
    void gradient(float const *input, float *grad) {{
       for (int i = 0; i < {N_IN}; ++i) grad[i] = 0.f;
       Session d_session;                                 // scratch adjoints (reverse pass)
       f_pullback(session, input, 1.f, &d_session, grad); // seed d_y = 1
    }}

    }}
    ''')

    Demo = getattr(ROOT, "Demo_" + model_name)

    value_func = Demo.value
    gradient_func = Demo.gradient

    return value_func, gradient_func, N_OUT
