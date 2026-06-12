#ifndef RTimeSeries_h
#define RTimeSeries_h

template <typename... Vecs>
void apply_permutation(const std::vector<std::size_t> &order, std::vector<Vecs> &...vecs)
{
   std::vector<bool> done(order.size(), false);

   for (std::size_t start = 0; start < order.size(); ++start) {
      if (done[start])
         continue;

      std::size_t idxFrom = start;
      std::size_t idxTo = order[start];

      if (idxFrom == idxTo) {
         done[idxFrom] = true;
         continue;
      }

      // Do cycle permutation across all vectors
      while (!done[idxFrom]) {
         idxTo = order[idxFrom];
         if (done[idxTo])
            break;

         // Swap element `idxFrom` with `idxTo` in all vectors
         ([&] { std::swap(vecs[idxFrom], vecs[idxTo]); }(), ...);

         done[idxFrom] = true;
         idxFrom = idxTo;
      }
   }
}

template <class T>
std::vector<std::size_t> make_sort_permutation(std::vector<T> const &vec)
{
   std::vector<std::size_t> idx(vec.size());
   std::iota(idx.begin(), idx.end(), 0);

   std::sort(idx.begin(), idx.end(), [&](std::size_t i, std::size_t j) { return vec[i] < vec[j]; });

   return idx;
}

// Convert time_t to "YYYY-MM" string
// TODO: Have dedicated "period" types for type safety.
std::string time_to_period(std::time_t ts)
{
   std::tm tm = *std::localtime(&ts);
   std::ostringstream oss;
   oss << (tm.tm_year + 1900) << "-";
   if (tm.tm_mon + 1 < 10)
      oss << "0";
   oss << (tm.tm_mon + 1);
   return oss.str();
}

// Helper to parse a date string "MM/DD/YYYY" -> time_t
std::time_t parse_date(const std::string &s)
{
   std::tm tm = {};
   std::istringstream ss(s);
   ss >> std::get_time(&tm, "%m/%d/%Y");
   return std::mktime(&tm);
}

// Helper to remove $ and commas and convert to double
double parse_price(std::string s)
{
   s.erase(std::remove(s.begin(), s.end(), '$'), s.end());
   return std::stod(s);
}

struct InputTransformer {

   static void transform(std::vector<double> &output, const double *x)
   {
      output.resize(3);
      const double open = x[0];
      const double close = x[1];
      const double low = x[2];
      const double high = x[3];
      const double volume = x[4];
      output[0] = (close - open) / open;             // DiffRel
      output[1] = volume * open;                     // Total Volume
      output[2] = (high - low) / (0.5 * high + low); // Vola
   }

   constexpr static std::size_t nOut = 3;
};

void fitLinearModel(std::size_t nFeatures, std::size_t nSamples, const double *x, const double *y, double *coef,
                    double *intercept)
{
   std::vector<double> vars;

   std::size_t nInputs = InputTransformer::nOut;
   // "hypN" means a hyperplane: p0 + p1*x1 + p2*x2
   TLinearFitter fitter(nInputs, ("hyp" + std::to_string(nInputs)).c_str());

   for (std::size_t i = 0; i < nSamples; ++i) {
      InputTransformer::transform(vars, x + i * nFeatures);
      fitter.AddPoint(vars.data(), y[i]);
   }

   fitter.Eval();

   intercept[0] = fitter.GetParameter(0);
   for (int i = 0; i < vars.size(); ++i) {
      coef[i] = fitter.GetParameter(i + 1);
   }
}

void evaluateLinearModel(double *out, std::size_t offset, std::size_t nFeatures, std::size_t nSamples, const double *x,
                         const double *coef, double intercept)
{
   std::vector<double> vars;

   for (std::size_t i = 0; i < nSamples; ++i) {
      out[offset + i] += intercept;
      InputTransformer::transform(vars, x + i * nFeatures);
      for (std::size_t j = 0; j < vars.size(); ++j) {
         out[offset + i] += coef[j] * vars[j];
      }
   }
}

template<class DataStruct_t>
auto read_csv(std::string const &filename, char delimiter = ',', int skiprows = 0)
{
   //DataStruct::SoA data;
   DataStruct_t data;

   std::ifstream file(filename.c_str());
   if (!file.is_open()) {
      std::cerr << "Cannot open CSV file!" << std::endl;
      return data;
   }

   std::string line;

   for (int i = 0; i < skiprows; ++i) {
    std::getline(file, line);
   }

   std::vector<std::string> buffers(6);

   while (std::getline(file, line)) {
      if (line.empty())
         continue;

      std::stringstream ss(line);

      for (std::size_t i = 0; i < buffers.size(); ++i) {
         std::getline(ss, buffers[i], delimiter);
      }

      data.date.push_back(parse_date(buffers[0]));
      data.close.push_back(parse_price(buffers[1]));
      data.volume.push_back(std::stoll(buffers[2]));
      data.open.push_back(parse_price(buffers[3]));
      data.high.push_back(parse_price(buffers[4]));
      data.low.push_back(parse_price(buffers[5]));
   }

   return data;
}

#endif
