"""D2 bootstrap: reject wrong preloaded origins before calling the existing CLI.

All imports and checks run in the globally timed Job. Later callback checks run
before the play backend enters launch_simulation or constructs the Task environment.
"""

import os
import sys

# This script is pinned in preparation; actual observers come from this arm's verified copy.
sys.path.insert(0, os.environ["FR3_DIAG_EVIDENCE"])
import d2_origins as origins


def main():
    manifest = origins.identity()
    pins = origins.module_pins(manifest, include_main=True)
    origins.check_loaded(pins, "bootstrap_preloaded", required=("__main__", "d2_origins"))
    from fr3_contact import register

    origins.check_loaded(pins, "bootstrap_task_package", required=("fr3_contact",))
    origins.check_symbol(register, pins["fr3_contact"], "Task register")
    register()
    origins.registration(manifest)
    from isaaclab.cli import cli

    cli()


if __name__ == "__main__":
    main()
