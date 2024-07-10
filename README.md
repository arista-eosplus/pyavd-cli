# PyAVD cli tools

A set of tools on top of [PyAVD](https://avd.arista.com/4.8/docs/pyavd.html)
to process AVD configurations with python.

## Quick start

Install with pip inside a virtual environment:

```shell
$> python -m venv .venv
$> source .venv/bin/activate
$> pip install pyavd==<pyavd version> pyavd-cli
$> pyavd-build -i inventory.yml -f FABRIC -v
```

## pyavd-build

It "builds" EOS cli configs from AVD config. Similar to the process done by an ansible
playbook that invokes the AVD roles [eos_designs](https://avd.arista.com/4.8/roles/eos_designs/index.html) and
[eos_cli_config_gen](https://avd.arista.com/4.8/roles/eos_cli_config_gen/index.html).

It uses ansible Inventory Manager to read the AVD inventory so ansible features work out of the box.
It supports inline jinja templates and custom interface description/ip addressing via python modules.

```shell
$> pyavd-build --help
usage: pyavd-build [-h] -i INVENTORY_PATH [-o CONFIG_OUTPUT_PATH] [--avd-facts-path AVD_FACTS_PATH] -f
                   FABRIC_GROUP_NAME [-l LIMIT] [-m MAX_WORKERS] [--strict] [--vault-id [VAULT_ID ...]] [-v]

Build AVD fabric.

options:
  -h, --help            show this help message and exit
  -i INVENTORY_PATH, --inventory-path INVENTORY_PATH
                        Path to the inventory file.
  -o CONFIG_OUTPUT_PATH, --config-output-path CONFIG_OUTPUT_PATH
                        Path to the output directory.
  --avd-facts-path AVD_FACTS_PATH
                        If provided AVD facts will be written to this path.
  -f FABRIC_GROUP_NAME, --fabric-group-name FABRIC_GROUP_NAME
                        Name of the fabric group.
  -l LIMIT, --limit LIMIT
                        Limit filter for inventory.
  -m MAX_WORKERS, --max-workers MAX_WORKERS
                        Maximum number of parallel workers.
  --strict              Use strict mode and fail if there is validation errors
  --vault-id [VAULT_ID ...]
                        Vault ID used to decrypt the inventory. Multiple vault IDs can be provided.
  -v, --verbose
```
