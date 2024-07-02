# Copyright (c) 2024 Arista Networks, Inc.
# Use of this source code is governed by the Apache License 2.0
# that can be found in the LICENSE file.

import argparse
import logging
import os
import time
from concurrent.futures import Executor, ProcessPoolExecutor, as_completed
from functools import wraps
from pathlib import Path
from typing import Callable, List, Optional

import yaml
from ansible.cli import CLI  # type: ignore
from ansible.inventory.manager import InventoryManager  # type: ignore
from ansible.parsing.dataloader import DataLoader  # type: ignore
from ansible.parsing.yaml.dumper import AnsibleDumper  # type: ignore
from ansible.plugins.loader import init_plugin_loader  # type: ignore
from ansible.template import Templar  # type: ignore
from ansible.vars.manager import VariableManager  # type: ignore
from pyavd import ValidationResult  # type: ignore
from pyavd import __version__ as pyavd_version  # type: ignore
from pyavd import (
    get_avd_facts,
    get_device_config,
    get_device_structured_config,
    validate_inputs,
    validate_structured_config,
)

os.environ["PYAVD"] = "1"

logger = logging.getLogger()


def log_execution_time(logger_fn: Callable = logger.debug, log_prefix: Optional[str] = None) -> Callable:
    def decorator_log_execution_time(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            start = time.perf_counter()
            value = func(*args, **kwargs)
            logger_fn("%s: %fs", log_prefix or func.__name__, (time.perf_counter() - start))
            return value

        return wrapper

    return decorator_log_execution_time


def log_host_validation_result(hostname: str, result: ValidationResult) -> None:
    for validation_error in result.validation_errors:
        logger.error("%s: %s", hostname, validation_error)

    for deprecation_warning in result.deprecation_warnings:
        logger.warning("%s: %s", hostname, deprecation_warning)


def validate_hostvars(hostname: str, hostvars: dict, strict: bool):
    validation_result = validate_inputs(hostvars)

    log_host_validation_result(hostname, validation_result)

    if validation_result.failed and strict:
        raise RuntimeError(f"{hostname} validate_inputs failed")

    return hostname, hostvars


def build_structured_config(hostname: str, inputs: dict, avd_facts: dict):
    try:
        structured_config = get_device_structured_config(hostname, inputs, avd_facts=avd_facts)
    except Exception as exc:  # as of pyavd 4.5.0 AristaAvdDuplicateDataError can't be pickled, wrap exceptions with RuntimeError
        raise RuntimeError(f"{exc}") from exc

    return hostname, structured_config


def build_device_config(hostname: str, structured_config: dict, strict: bool):
    validation_result = validate_structured_config(structured_config)

    log_host_validation_result(hostname, validation_result)

    if validation_result.failed and strict:
        raise RuntimeError(f"{hostname} validate_structured_config failed")

    return hostname, get_device_config(structured_config)


@log_execution_time(log_prefix="Validate inputs time")
def validate_all_inputs(all_hostvars: dict, strict: bool, executor: Executor) -> dict:
    validated_inputs = {}
    futures = [executor.submit(validate_hostvars, hostname, hostvars, strict) for hostname, hostvars in all_hostvars.items()]
    for future in as_completed(futures):
        hostname, hostvars = future.result()
        validated_inputs[hostname] = hostvars

    return validated_inputs


@log_execution_time(log_prefix="Build structured config time")
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


@log_execution_time(log_prefix="Build device config time")
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


@log_execution_time(log_prefix="Total build time")
def build(  # pylint: disable=too-many-arguments,too-many-locals
    inventory_path: Path,
    fabric_name: str,
    limit: str,
    intended_configs_path: Path,
    structured_configs_path: Path,
    avd_facts_path: Optional[Path] = None,
    max_workers: int = 10,
    strict: bool = False,
    vault_ids: Optional[List[str]] = None,
):
    init_plugin_loader()

    loader = DataLoader()
    if vault_ids:
        CLI.setup_vault_secrets(loader, vault_ids=vault_ids)
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
        all_hostvars = validate_all_inputs(all_hostvars, strict, executor)

        # Generate facts
        avd_facts = log_execution_time(log_prefix="Generate facts time")(get_avd_facts)(all_hostvars)
        if avd_facts_path is not None:
            avd_facts_path.parent.mkdir(parents=True, exist_ok=True)
            with open(avd_facts_path, mode="w", encoding="utf8") as fd:
                yaml.dump(
                    avd_facts,
                    fd,
                    Dumper=AnsibleDumper,
                    indent=2,
                    sort_keys=False,
                    width=130,
                )

        limit_hostvars = {hostname: hostvars for hostname, hostvars in all_hostvars.items() if hostname in limit_hostnames}

        # Build structured config
        structured_configs = build_and_write_all_structured_configs(
            limit_hostvars, avd_facts, structured_configs_path, templar, executor
        )

        # Generate device config
        build_and_write_all_device_configs(intended_configs_path, structured_configs, strict, executor)


def main():
    parser = argparse.ArgumentParser(description="Build AVD fabric.")
    parser.add_argument("-i", "--inventory-path", required=True, type=Path, help="Path to the inventory file.")
    parser.add_argument("-o", "--config-output-path", default=Path("intended"), type=Path, help="Path to the output directory.")
    parser.add_argument("--avd-facts-path", type=Path, help="If provided AVD facts will be written to this path.")
    parser.add_argument("-f", "--fabric-group-name", required=True, type=str, help="Name of the fabric group.")
    parser.add_argument(
        "-l",
        "--limit",
        default="all,!cvp",
        type=str,
        help=(
            "Limit filter for inventory. See https://docs.ansible.com/ansible/latest/inventory_guide/intro_patterns.html"
            "#patterns-and-ad-hoc-commands."
        ),
    )
    parser.add_argument("-m", "--max-workers", default=os.cpu_count(), type=int, help="Maximum number of parallel workers.")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Use strict mode and fail if there is validation errors",
    )
    parser.add_argument(
        "--vault-id",
        type=str,
        action="extend",
        nargs="*",
        help=(
            "Vault ID used to decrypt the inventory. Multiple vault IDs can be provided. See "
            "https://docs.ansible.com/ansible/latest/vault_guide/vault_using_encrypted_content.html#passing-vault-ids"
        ),
    )
    parser.add_argument("-v", "--verbose", action="store_true")

    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)

    inventory_path = args.inventory_path
    config_output_path = args.config_output_path
    intended_configs_path = config_output_path / "configs"
    structured_configs_path = config_output_path / "structured_configs"
    avd_facts_path = args.avd_facts_path
    max_workers = args.max_workers
    strict = args.strict
    fabric_group_name = args.fabric_group_name
    limit = args.limit
    vault_ids = args.vault_id

    logger.debug("pyavd version: %s", pyavd_version)
    logger.debug("inventory_path: %s", inventory_path)
    logger.debug("intended_configs_path: %s", intended_configs_path)
    logger.debug("structured_configs_path: %s", structured_configs_path)
    logger.debug("avd_facts_path: %s", avd_facts_path)
    logger.debug("max_workers: %s", max_workers)
    logger.debug("strict: %s", strict)
    logger.debug("fabric_group_name: %s", fabric_group_name)
    logger.debug("limit: %s", limit)
    logger.debug("vault_ids: %s", vault_ids)

    build(
        inventory_path=inventory_path,
        fabric_name=fabric_group_name,
        limit=limit,
        intended_configs_path=intended_configs_path,
        structured_configs_path=structured_configs_path,
        avd_facts_path=avd_facts_path,
        max_workers=max_workers,
        strict=strict,
        vault_ids=vault_ids,
    )


if __name__ == "__main__":
    main()
