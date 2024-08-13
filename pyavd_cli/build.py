# Copyright (c) 2024 Arista Networks, Inc.
# Use of this source code is governed by the Apache License 2.0
# that can be found in the LICENSE file.

import argparse
import logging
import os
import sys
import time
from concurrent.futures import Executor, ProcessPoolExecutor
from functools import partial, wraps
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

logger = logging.getLogger("pyavd-build")


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


def validate_hostvars(hostname: str, hostvars: dict, strict: bool = False):
    validation_result = validate_inputs(hostvars)

    log_host_validation_result(hostname, validation_result)

    if validation_result.failed and strict:
        raise RuntimeError(f"{hostname} validate_inputs failed")

    return hostname, hostvars


def build_and_write_device_config(  # pylint: disable=too-many-arguments
    hostname: str,
    inputs: dict,
    avd_facts: dict,
    structured_configs_path: Path,
    intended_configs_path: Path,
    strict: bool = False,
):
    try:
        structured_config = get_device_structured_config(hostname, inputs, avd_facts=avd_facts)
    except Exception as exc:  # as of pyavd 4.5.0 AristaAvdDuplicateDataError can't be pickled, wrap exceptions with RuntimeError
        raise RuntimeError(f"{exc}") from exc

    templar = Templar(loader=DataLoader(), variables=avd_facts["avd_switch_facts"][hostname] | structured_config)
    template_structured_config = templar.template(structured_config)

    # Write structured config
    with open(structured_configs_path / f"{hostname}.yml", mode="w", encoding="utf8") as fd:
        yaml.dump(
            template_structured_config,
            fd,
            Dumper=AnsibleDumper,
            indent=2,
            sort_keys=False,
            width=130,
        )

    validation_result = validate_structured_config(template_structured_config)

    log_host_validation_result(hostname, validation_result)

    if validation_result.failed and strict:
        raise RuntimeError(f"{hostname} validate_structured_config failed")

    # Write device configs
    with open(intended_configs_path / f"{hostname}.cfg", mode="w", encoding="utf8") as fd:
        fd.write(get_device_config(template_structured_config))

    return hostname


@log_execution_time(log_prefix="Load inputs time")
def get_fabric_hostvars(fabric_name: str, inventory: InventoryManager, loader: DataLoader) -> dict:
    variable_manager = VariableManager(loader=loader, inventory=inventory)
    templar = Templar(loader=loader)

    all_hostvars = {}
    for host in inventory.get_hosts(pattern=fabric_name):
        hostvars = variable_manager.get_vars(host=inventory.get_host(host.name))
        templar.available_variables = hostvars
        template_hostvars = templar.template(hostvars, fail_on_undefined=False)
        all_hostvars[host.name] = template_hostvars

    return all_hostvars


@log_execution_time(log_prefix="Validate inputs time")
def validate_all_inputs(all_hostvars: dict, strict: bool, executor: Executor) -> dict:
    return dict(
        executor.map(
            partial(
                validate_hostvars,
                strict=strict,
            ),
            all_hostvars.keys(),
            all_hostvars.values(),
            chunksize=16,
        )
    )


@log_execution_time(log_prefix="Generate facts time")
def generate_avd_facts(all_hostvars: dict, avd_facts_path: Optional[Path] = None):
    avd_facts = get_avd_facts(all_hostvars)
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
    return avd_facts


@log_execution_time(log_prefix="Build and write device config time")
def build_and_write_all_device_configs(  # pylint: disable=too-many-arguments
    all_hostvars: dict,
    avd_facts: dict,
    structured_configs_path: Path,
    intended_configs_path: Path,
    strict: bool,
    executor: Executor,
) -> list:
    structured_configs_path.mkdir(parents=True, exist_ok=True)
    intended_configs_path.mkdir(parents=True, exist_ok=True)

    processed_hostnames = list(
        executor.map(
            partial(
                build_and_write_device_config,
                avd_facts=avd_facts,
                structured_configs_path=structured_configs_path,
                intended_configs_path=intended_configs_path,
                strict=strict,
            ),
            all_hostvars.keys(),
            all_hostvars.values(),
            chunksize=8,
        )
    )

    return processed_hostnames


@log_execution_time(log_prefix="Total build time")
def build(  # pylint: disable=too-many-arguments
    fabric_hostvars: dict,
    target_hosts: List[str],
    intended_configs_path: Path,
    structured_configs_path: Path,
    avd_facts_path: Optional[Path] = None,
    max_workers: Optional[int] = None,
    strict: bool = False,
):
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        # Validate inputs
        validated_fabric_hostvars = validate_all_inputs(all_hostvars=fabric_hostvars, strict=strict, executor=executor)

        # Generate facts
        avd_facts = generate_avd_facts(all_hostvars=validated_fabric_hostvars, avd_facts_path=avd_facts_path)

        target_hostvars = {
            hostname: hostvars for hostname, hostvars in validated_fabric_hostvars.items() if hostname in target_hosts
        }

        # Build and write device configs
        n_processed_hosts = len(
            build_and_write_all_device_configs(
                all_hostvars=target_hostvars,
                avd_facts=avd_facts,
                structured_configs_path=structured_configs_path,
                intended_configs_path=intended_configs_path,
                strict=strict,
                executor=executor,
            )
        )
    logger.debug("Processed %d hosts", n_processed_hosts)


def main():
    parser = argparse.ArgumentParser(description="Build AVD fabric.")
    parser.add_argument("-i", "--inventory-path", required=True, type=Path, help="Path to the inventory file.")
    parser.add_argument("-o", "--config-output-path", default=Path("intended"), type=Path, help="Path to the output directory.")
    parser.add_argument("--avd-facts-path", type=Path, help="If provided AVD facts will be written to this path.")
    parser.add_argument("-f", "--fabric-group-name", required=True, type=str, help="Name of the fabric group.")
    parser.add_argument(
        "-l",
        "--limit",
        default=None,
        type=str,
        help=(
            "Limit filter for inventory. See https://docs.ansible.com/ansible/latest/inventory_guide/intro_patterns.html"
            "#patterns-and-ad-hoc-commands. If not set it will default to fabric-group-name"
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

    inventory_path = args.inventory_path.resolve()
    config_output_path = args.config_output_path.resolve()
    intended_configs_path = config_output_path / "configs"
    structured_configs_path = config_output_path / "structured_configs"
    avd_facts_path = args.avd_facts_path.resolve() if args.avd_facts_path else None
    limit = args.limit or args.fabric_group_name

    logger.debug("pyavd version: %s", pyavd_version)
    logger.debug("inventory_path: %s", inventory_path)
    logger.debug("intended_configs_path: %s", intended_configs_path)
    logger.debug("structured_configs_path: %s", structured_configs_path)
    logger.debug("avd_facts_path: %s", avd_facts_path)
    logger.debug("max_workers: %s", args.max_workers)
    logger.debug("strict: %s", args.strict)
    logger.debug("fabric_group_name: %s", args.fabric_group_name)
    logger.debug("limit: %s", limit)
    logger.debug("vault_ids: %s", args.vault_id)

    # load inventory
    init_plugin_loader()
    loader = DataLoader()
    if args.vault_id:
        CLI.setup_vault_secrets(loader, vault_ids=args.vault_id)
    inventory_manager = InventoryManager(loader=loader, sources=[inventory_path.as_posix()])

    fabric_hostvars = get_fabric_hostvars(args.fabric_group_name, inventory_manager, loader)

    target_hosts = [host.name for host in inventory_manager.get_hosts(pattern=limit)]
    if len(target_hosts) == 0:
        logger.error("No hosts matched pattern=%s", limit)
        sys.exit(1)

    if set(fabric_hostvars.keys()).isdisjoint(target_hosts):
        logger.error("No hosts from group %s selected with pattern=%s", args.fabric_group_name, limit)
        sys.exit(1)

    build(
        fabric_hostvars=fabric_hostvars,
        target_hosts=target_hosts,
        intended_configs_path=intended_configs_path,
        structured_configs_path=structured_configs_path,
        avd_facts_path=avd_facts_path,
        max_workers=args.max_workers,
        strict=args.strict,
    )


if __name__ == "__main__":
    main()
