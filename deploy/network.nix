{
  genomics-root =
    {
      modulesPath,
      lib,
      name,
      pkgs,
      ...
    }:
    let
      pyEnv = pkgs.python3.withPackages (
        ps: with ps; [
          ipykernel
          jupyterhub
          jupyterlab
          matplotlib
          numba
          numpy
          pandas
          root
          torch
          xgboost
          notebook
        ]
      );
    in
    {
      imports = lib.optional (builtins.pathExists ./do-userdata.nix) ./do-userdata.nix ++ [
        (modulesPath + "/virtualisation/digital-ocean-config.nix")
      ];

      deployment.targetHost = # to fill out, e.g. "123.45.67.890";
      deployment.targetUser = "root";

      networking.hostName = name;

      nixpkgs.hostPlatform = "x86_64-linux";
      system.stateVersion = "26.05";

      # Networking / Firewall
      # For http  : [ 22 8000 ]
      # For https : [22 80 443]
      networking.firewall.enable = true;
      networking.firewall.allowedTCPPorts = [
        22
        80
        443
      ];

      environment.systemPackages = with pkgs; [
        bash
        glibc
        root
      ];

      # Gives us /bin/bash, required by the "!" notebook magic
      programs.bash.enable = true;
      systemd.tmpfiles.rules = [
        "L /bin/bash - - - - /run/current-system/sw/bin/bash"
      ];

      services.jupyterhub = {
        enable = true;

        # Listen on all interfaces (important for DO)
        host = "0.0.0.0";
        port = 8000;

        # Simple authenticator for demo purposes
        extraConfig = ''
          import os

          from jupyterhub.auth import DummyAuthenticator

          # Add system binaries, which ROOT relies on
          os.environ["PATH"] = "/run/current-system/sw/bin:" + os.environ.get("PATH","")

          c.JupyterHub.authenticator_class = DummyAuthenticator

          # Set password as as string here, and distribute to the demo audience
          c.DummyAuthenticator.password =

          # explicitly define single-user command
          c.Spawner.cmd = ["${pyEnv}/bin/jupyter-labhub"]

          # For reverse proxy when using https
          # Comment out the next two lines if you just want to use http
          c.JupyterHub.bind_url = "http://127.0.0.1:8000"
          c.JupyterHub.trust_xheaders = True

          def ensure_user(spawner):
              import subprocess
              import pwd

              username = spawner.user.name
              notebook_dir = "/home/" + username

              try:
                  pwd.getpwnam(username)
              except KeyError:
                  # user doesn't exist, create it
                  subprocess.run(["sudo", "useradd", "-m", username], check=True)
                  #subprocess.run(["sudo", "su", username, "-c", f'"mkdir -p {notebook_dir}'], check=True)

              spawner.notebook_dir = notebook_dir

              # Automatically install the pyEnv kernel for this user
              # Note: --user writes to ~/.local/share/jupyter for that user
              subprocess.run([
                  "sudo", "-u", username,
                  "${pyEnv}/bin/python3",
                  "-m", "ipykernel",
                  "install",
                  "--user",
                  "--name=pyenv",
                  "--display-name=Python 3 with packages"
              ], check=True)

              # Full Nix environment PATH for the kernel
              nix_path = "/run/current-system/sw/bin"

              # Prepend nix path to PATH inside kernel
              import json
              import os.path as p
              kernel_dir = p.expanduser(f"~{username}/.local/share/jupyter/kernels/pyenv")
              kernel_json = p.join(kernel_dir, "kernel.json")
              with open(kernel_json, "r") as f:
                  data = json.load(f)

              data["env"] = {"PATH": f"{nix_path}:" + os.environ.get("PATH", "")}

              # Save changes
              with open(kernel_json, "w") as f:
                  json.dump(data, f, indent=2)


          c.Spawner.pre_spawn_hook = ensure_user
        '';
      };

      security.sudo.extraRules = [
        {
          users = [ "jupyterhub" ];
          commands = [
            {
              command = "/run/current-system/sw/bin/useradd";
              options = [ "NOPASSWD" ];
            }
            {
              command = "/run/current-system/sw/bin/su";
              options = [ "NOPASSWD" ];
            }
          ];
        }
      ];

      # The "services.nginx" and "security.acme" entries are only relevant for
      # https. When you only need http, you can comment them out.
      services.nginx = {
        enable = true;

        recommendedProxySettings = true;
        recommendedTlsSettings = true;

        # Insert your actual URL as a string, like "jupyterhub.root.cern", and
        # don't forget to set the DNS record for that (sub)domain to point to
        # the server IP.
        virtualHosts."<sub.myurl.com>" = {
          enableACME = true;
          forceSSL = true;

          locations."/" = {
            proxyPass = "http://127.0.0.1:8000";
            proxyWebsockets = true; # IMPORTANT for JupyterHub
          };
        };
      };
      security.acme = {
        acceptTerms = true;
        defaults.email = # "<myname@mymail.com>";
      };
    };
}
