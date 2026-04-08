
Based on this [very good blogpost about deploying NixOS on DigitalOcean](https://justinas.org/nixos-in-the-cloud-step-by-step-part-1), this directory contains a `network.nix` configuration that declares a multi-user **JupyterHub** environment with ROOT installed. It can be readily deployed with `morph deploy network.nix switch` to a DigitalOcean droplet.

Please update the IP address, hostname, and login password before deploying.

You can login with any username to the JupyterHub. A corresponding user with home directory will be automatically created if it doesn't exist yet. This allows people to have a persistent environment for trying out ROOT.

Future plans:
  * HTTPS support
  * Automatic cloning of the demo code from this repository when a new user is created.
