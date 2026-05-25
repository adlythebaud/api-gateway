import sys

import yaml

from gateway.config import get_config_path, load_config


def main():
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


if __name__ == "__main__":
    main()
