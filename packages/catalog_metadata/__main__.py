"""Launch the catalog host without loading the laboratory application."""

import argparse
import os

import uvicorn


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8011)
    arguments = parser.parse_args()
    if arguments.host not in ("127.0.0.1", "localhost", "::1") and not os.getenv("LW_CATALOG_OPERATOR_TOKEN"):
        parser.error("Set LW_CATALOG_OPERATOR_TOKEN before binding a non-loopback interface")
    uvicorn.run(
        "packages.catalog_metadata.service:create_app", factory=True, host=arguments.host, port=arguments.port
    )


if __name__ == "__main__":
    main()
