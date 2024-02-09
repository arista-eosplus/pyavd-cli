# pylint: disable=missing-module-docstring,missing-function-docstring

import argparse
import logging
import os
import time
from concurrent.futures import Executor, ProcessPoolExecutor, as_completed
from pathlib import Path

import yaml
from ansible.inventory.manager import InventoryManager  # type: ignore
from ansible.parsing.dataloader import DataLoader  # type: ignore
from ansible.parsing.yaml.dumper import AnsibleDumper  # type: ignore
from ansible.plugins.loader import init_plugin_loader  # type: ignore
from ansible.template import Templar  # type: ignore
from ansible.vars.manager import VariableManager  # type: ignore
from pyavd import (  # type: ignore
    get_avd_facts,
    get_device_config,
    get_device_structured_config,
    validate_inputs,
    validate_structured_config,
)

os.environ["PYAVD"] = "1"

logger = logging.getLogger()


def validate_hostvars(hostname: str, hostvars: dict, strict: bool):
    results = validate_inputs(hostvars)
    if results.failed:
        for result in results.validation_errors:
            logger.error(result)
        if strict:
            raise RuntimeError(f"{hostname} validate_inputs failed")

    return hostname, hostvars


def build_structured_config(hostname: str, inputs: dict, avd_facts: dict):
    try:
        structured_config = get_device_structured_config(hostname, inputs, avd_facts=avd_facts)
    except Exception as exc:  # as of pyavd 4.5.0 AristaAvdDuplicateDataError can't be pickled, wrap exceptions with RuntimeError
        raise RuntimeError(f"{exc}") from exc

    return hostname, structured_config


def build_device_config(hostname: str, structured_config: dict, strict: bool):
    results = validate_structured_config(structured_config)
    if results.failed:
        for result in results.validation_errors:
            logger.error(result)
        if strict:
            raise RuntimeError(f"{hostname} validate_structured_config failed")

    return hostname, get_device_config(structured_config)


def build(  # pylint: disable=too-many-arguments,too-many-locals
    inventory_path: Path,
    fabric_name: str,
    limit: str,
    intended_configs_path: Path,
    structured_configs_path: Path,
    max_workers: int = 10,
    strict: bool = False,
):
    init_plugin_loader()

    loader = DataLoader()
    inventory = InventoryManager(loader=loader, sources=[inventory_path.as_posix()])
    variable_manager = VariableManager(loader=loader, inventory=inventory)

    templar = Templar(loader=loader)

    all_hostvars = {}
    for host in inventory.get_hosts(pattern=fabric_name):
        hostvars = variable_manager.get_vars(host=inventory.get_host(host.name))
        templar.available_variables = hostvars
        template_hostvars = templar.template(hostvars, fail_on_undefined=False)
        all_hostvars[host.name] = template_hostvars

    limit_hostnames = [host.name for host in inventory.get_hosts(pattern=limit)]

    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        # Validate inputs
        start = time.time()
        all_hostvars = validate_all_inputs(all_hostvars, strict, executor)
        logger.debug("Validate inputs time: %ds", (time.time() - start))

        # Generate facts
        start = time.time()
        avd_facts = get_avd_facts(all_hostvars)
        logger.debug("Generate facts time: %ds", (time.time() - start))

        limit_hostvars = {hostname: hostvars for hostname, hostvars in all_hostvars.items() if hostname in limit_hostnames}

        # Build structured config
        start = time.time()
        structured_configs = build_and_write_all_structured_configs(
            limit_hostvars, avd_facts, structured_configs_path, templar, executor
        )
        logger.debug("Build structured config time: %ds", (time.time() - start))

        start = time.time()
        build_and_write_all_device_configs(intended_configs_path, structured_configs, strict, executor)
        logger.debug("Build designed config time: %ds", (time.time() - start))


def validate_all_inputs(all_hostvars: dict, strict: bool, executor: Executor) -> dict:
    validated_inputs = {}
    futures = [executor.submit(validate_hostvars, hostname, hostvars, strict) for hostname, hostvars in all_hostvars.items()]
    for future in as_completed(futures):
        hostname, hostvars = future.result()
        validated_inputs[hostname] = hostvars

    return validated_inputs


def build_and_write_all_structured_configs(
    all_hostvars: dict,
    avd_facts: dict,
    structured_configs_path: Path,
    templar: Templar,
    executor: Executor,
) -> dict:
    structured_configs = {}
    futures = [
        executor.submit(build_structured_config, hostname, hostvars, avd_facts) for hostname, hostvars in all_hostvars.items()
    ]
    # Write structured configs
    structured_configs_path.mkdir(parents=True, exist_ok=True)
    for future in as_completed(futures):
        hostname, structured_config = future.result()

        templar.available_variables = avd_facts["avd_switch_facts"][hostname] | structured_config
        template_structured_config = templar.template(structured_config)
        structured_configs[hostname] = template_structured_config

        with open(structured_configs_path / f"{hostname}.yml", mode="w", encoding="utf8") as fd:
            yaml.dump(
                structured_configs[hostname],
                fd,
                Dumper=AnsibleDumper,
                indent=2,
                sort_keys=False,
                width=130,
            )
    return structured_configs


def build_and_write_all_device_configs(
    intended_configs_path: Path,
    structured_configs: dict,
    strict: bool,
    executor: Executor,
):
    # Build device config
    futures = [
        executor.submit(build_device_config, hostname, structured_config, strict)
        for hostname, structured_config in structured_configs.items()
    ]
    # Write device configs
    intended_configs_path.mkdir(parents=True, exist_ok=True)
    for future in as_completed(futures):
        hostname, device_config = future.result()

        with open(intended_configs_path / f"{hostname}.cfg", mode="w", encoding="utf8") as fd:
            fd.write(device_config)


def main():
    parser = argparse.ArgumentParser(description="Build AVD fabric.")
    parser.add_argument("-i", "--inventory-path", required=True, type=Path)
    parser.add_argument("-o", "--config-output-path", default=Path("intended"), type=Path)
    parser.add_argument("-f", "--fabric-group-name", required=True, type=str)
    parser.add_argument("-l", "--limit", default="all,!cvp", type=str)
    parser.add_argument("-m", "--max-workers", default=os.cpu_count(), type=int)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Use strict mode and fail if there is validation errors",
    )
    parser.add_argument("-v", "--verbose", action="store_true")

    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)

    inventory_path = args.inventory_path
    config_output_path = args.config_output_path
    intended_configs_path = config_output_path / "configs"
    structured_configs_path = config_output_path / "structured_configs"
    max_workers = args.max_workers
    strict = args.strict
    fabric_group_name = args.fabric_group_name
    limit = args.limit

    logger.debug("inventory_path: %s", inventory_path)
    logger.debug("intended_configs_path: %s", intended_configs_path)
    logger.debug("structured_configs_path: %s", structured_configs_path)
    logger.debug("max_workers: %s", max_workers)
    logger.debug("strict: %s", strict)
    logger.debug("fabric_group_name: %s", fabric_group_name)
    logger.debug("limit: %s", limit)

    start = time.time()
    build(
        inventory_path=inventory_path,
        fabric_name=fabric_group_name,
        limit=limit,
        intended_configs_path=intended_configs_path,
        structured_configs_path=structured_configs_path,
        max_workers=max_workers,
        strict=strict,
    )
    logger.info("Total build time: %ds", (time.time() - start))


if __name__ == "__main__":
    main()
