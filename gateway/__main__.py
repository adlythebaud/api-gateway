import sys

import yaml

from gateway.config import get_config_path, load_config
from gateway.server import create_server


def main():
    standalone = "--standalone" in sys.argv
    if standalone:
        sys.argv.remove("--standalone")

    config_path = get_config_path()

    try:
        config = load_config(config_path)
    except (ValueError, FileNotFoundError, yaml.YAMLError) as e:
        print(f"Failed to load config: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"GatewayKit loaded config from {config_path}")
    print(f"  Port: {config.gateway.port}")
    print(f"  Routes: {len(config.routes)}")
    for route in config.routes:
        print(f"    {route.methods} {route.path} -> {route.upstream.url or 'load-balanced'}")

    if standalone:
        from gateway.standalone import start_mock_upstreams
        servers = start_mock_upstreams(config)
        print(f"  Standalone mode: {len(servers)} mock upstream(s) started\n")

    server = create_server(config)
    print(f"GatewayKit listening on port {config.gateway.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.server_close()


if __name__ == "__main__":
    main()
