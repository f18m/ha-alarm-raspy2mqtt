#!/usr/bin/env python3

import yaml
import aiomqtt
import os
import platform
from datetime import datetime, timezone
from raspy2mqtt.constants import MqttDefaults, HomeAssistantDefaults, SeqMicroHatConstants, MiscAppDefaults

from schema import Schema, Optional, SchemaError, Regex
from importlib.metadata import version
from importlib.metadata import PackageNotFoundError

#
# Author: fmontorsi
# Created: Apr 2024
# License: Apache license
#


# =======================================================================================================
# AppConfig
# =======================================================================================================


class AppConfig:
    """
    This class represents the configuration of this application.
    It contains helpers to read the YAML config file for this utility plus helpers to
    receive configurations from CLI options and from environment variables.

    This class is also in charge of filling all values that might be missing in the config file
    with their defaults. All default constants are stored in constants.py
    """

    def __init__(self):
        self.config = None
        self.optoisolated_inputs_map = None  # None means "not loaded at all"

        # config options related to CLI options:
        self.disable_hw = False  # can be get/set from the outside
        self.verbose = False

        # technically speaking the version is not an "app config" but centralizing it here is handy
        try:
            self.app_version = str(version(MiscAppDefaults.THIS_APP_NAME))
        except PackageNotFoundError:
            # this happens when e.g. running unit tests inside Github runners where the wheel
            # package for this project is not installed:
            self.app_version = "N/A"

        self.current_hostname = platform.node()

        # before launching MQTT connections, define a unique MQTT prefix identifier:
        self.mqtt_identifier_prefix = "rpi2home_assistant_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

        self.mqtt_schema_for_sensor_on_and_off = Schema(
            {
                Optional("topic"): str,
                # the 'state_topic' makes sense only for OUTPUTs that have type=switch in HomeAssistant and
                # are required to publish a state topic
                Optional("state_topic"): str,
                Optional("payload_on"): str,
                Optional("payload_off"): str,
            }
        )
        self.mqtt_schema_for_edge_triggered_sensor = Schema(
            {
                Optional("topic"): str,
                # for edge-triggered sensors it's hard to propose a meaningful default payload...so it's not optional
                "payload": str,
            }
        )
        self.home_assistant_schema = Schema(
            {
                # device_class is required because it's hard to guess...
                "device_class": str,
                # the platform defaults to 'binary_sensor' for inputs and to 'switch' for outputs
                Optional("platform"): str,
                Optional("expire_after"): int,
                Optional("icon"): str,
            }
        )

        self.filter_schema = Schema(
            {
                Optional("stability_threshold_sec"): int,
            }
        )

        self.config_file_schema = Schema(
            {
                "mqtt_broker": {
                    "host": str,
                    Optional("port"): int,
                    Optional("reconnection_period_msec"): int,
                    Optional("user"): str,
                    Optional("password"): str,
                },
                Optional("home_assistant"): {
                    Optional("default_topic_prefix"): str,
                    Optional("publish_period_msec"): int,
                    Optional("discovery_messages"): {
                        Optional("enable"): bool,
                        Optional("topic_prefix"): str,
                        Optional("node_id"): str,
                    },
                },
                Optional("log_stats_every"): int,
                Optional("i2c_optoisolated_inputs"): [
                    {
                        "name": Regex(r"^[a-z0-9_]+$"),
                        Optional("description"): str,
                        "input_num": int,
                        "active_low": bool,
                        Optional("mqtt"): self.mqtt_schema_for_sensor_on_and_off,
                        "home_assistant": self.home_assistant_schema,
                        Optional("filter"): self.filter_schema,
                    }
                ],
                Optional("gpio_inputs"): [
                    {
                        "name": Regex(r"^[a-z0-9_]+$"),
                        Optional("description"): str,
                        "gpio": int,
                        "active_low": bool,
                        # mqtt is NOT optional for GPIO inputs... we need to have a meaningful payload to send
                        "mqtt": self.mqtt_schema_for_edge_triggered_sensor,
                        # home_assistant is not allowed for GPIO inputs, since they do not create binary_sensors
                    }
                ],
                Optional("outputs"): [
                    {
                        "name": Regex(r"^[a-z0-9_]+$"),
                        Optional("description"): str,
                        "gpio": int,
                        "active_low": bool,
                        Optional("mqtt"): self.mqtt_schema_for_sensor_on_and_off,
                        "home_assistant": self.home_assistant_schema,
                    }
                ],
            }
        )

    def check_gpio(self, idx: int):
        reserved_gpios = [
            SeqMicroHatConstants.SHUTDOWN_BUTTON_GPIO,
            SeqMicroHatConstants.INTERRUPT_GPIO,
            SeqMicroHatConstants.I2C_SDA,
            SeqMicroHatConstants.I2C_SCL,
        ]
        if idx < 1 or idx > 40:
            raise ValueError(
                f"Invalid GPIO index {idx}. The legal range is [1-40] since the Raspberry GPIO connector is a 40-pin connector."
            )
        # some GPIO pins are reserved and cannot be configured!
        if idx in reserved_gpios:
            raise ValueError(
                f"Invalid GPIO index {idx}: that GPIO pin is reserved for communication with the Sequent Microsystem HAT. Choose a different GPIO."
            )

    def populate_defaults_in_list_entry(
        self,
        entry_dict: dict,
        populate_mqtt: bool = True,
        populate_homeassistant: bool = True,
        has_payload_on_off: bool = True,
        has_state_topic: bool = True,
        is_output: bool = True,
        populate_filter: bool = True,
    ) -> dict:
        if "description" not in entry_dict:
            entry_dict["description"] = entry_dict["name"]

        if populate_mqtt:
            if "mqtt" not in entry_dict:
                entry_dict["mqtt"] = {}

            # an optional entry is the 'topic':
            if "topic" not in entry_dict["mqtt"]:
                entry_dict["mqtt"]["topic"] = f"{self.homeassistant_default_topic_prefix}/{entry_dict['name']}"
                print(f"Topic for {entry_dict['name']} defaults to [{entry_dict['mqtt']['topic']}]")

            if has_state_topic:
                if "state_topic" not in entry_dict["mqtt"]:
                    entry_dict["mqtt"][
                        "state_topic"
                    ] = f"{self.homeassistant_default_topic_prefix}/{entry_dict['name']}/state"
                    print(f"State topic for {entry_dict['name']} defaults to [{entry_dict['mqtt']['state_topic']}]")

            if has_payload_on_off:
                if "payload_on" not in entry_dict["mqtt"]:
                    entry_dict["mqtt"]["payload_on"] = MqttDefaults.PAYLOAD_ON
                if "payload_off" not in entry_dict["mqtt"]:
                    entry_dict["mqtt"]["payload_off"] = MqttDefaults.PAYLOAD_OFF

        if populate_homeassistant:
            # the following assertion is justified because 'schema' library should garantuee
            # that we get here only if all entries in the config file do have the 'home_assistant' section
            assert "home_assistant" in entry_dict

            if "expire_after" not in entry_dict["home_assistant"]:
                entry_dict["home_assistant"]["expire_after"] = HomeAssistantDefaults.EXPIRE_AFTER_SEC
                print(f"Expire-after for {entry_dict['name']} defaults to [{HomeAssistantDefaults.EXPIRE_AFTER_SEC}]")
            if "icon" not in entry_dict["home_assistant"]:
                entry_dict["home_assistant"]["icon"] = None
            if "platform" not in entry_dict["home_assistant"]:
                entry_dict["home_assistant"]["platform"] = "switch" if is_output else "binary_sensor"

        if populate_filter and not is_output:
            # filtering the output does not make sense, so the filter parameter is allowed only for inputs
            if "filter" not in entry_dict:
                entry_dict["filter"] = {}
            if "stability_threshold_sec" not in entry_dict["filter"]:
                entry_dict["filter"]["stability_threshold_sec"] = 0  # 0 means filtering is disabled
        return entry_dict

    def load(self, cfg_yaml: str) -> bool:
        print(f"Loading configuration file {cfg_yaml}")
        try:
            with open(cfg_yaml, "r") as file:
                self.config = yaml.safe_load(file)
        except FileNotFoundError:
            print(f"Error: configuration file '{cfg_yaml}' not found.")
            return False
        except yaml.YAMLError as e:
            print(f"Error parsing YAML config file '{cfg_yaml}': {e}")
            return False

        # validate the config against its schema:
        try:
            self.config_file_schema.validate(self.config)
        except SchemaError as e:
            print("Failed YAML config file validation. Error follows.")
            print(e)
            return False

        try:
            # convert the 'i2c_optoisolated_inputs' part in a dictionary indexed by the DIGITAL INPUT CHANNEL NUMBER:
            self.optoisolated_inputs_map = {}

            if "i2c_optoisolated_inputs" not in self.config:
                # empty list: feature disabled
                self.config["i2c_optoisolated_inputs"] = []

            for input_item in self.config["i2c_optoisolated_inputs"]:
                input_item = self.populate_defaults_in_list_entry(
                    input_item, has_state_topic=False, is_output=False, populate_filter=True
                )

                # check GPIO index
                idx = int(input_item["input_num"])
                if idx < 1 or idx > 16:
                    raise ValueError(
                        f"Invalid input_num {idx} for entry [{input_item['name']}]: the legal range is [1-16] since the Sequent Microsystem HAT only handles 16 inputs."
                    )
                if idx in self.optoisolated_inputs_map:
                    raise ValueError(
                        f"Invalid input_num {idx} for entry [{input_item['name']}]: such index for the Sequent Microsystem HAT input has already been used. Check again the configuration."
                    )

                # check HomeAssistant section
                if input_item["home_assistant"]["platform"] != "binary_sensor":
                    raise ValueError(
                        f"Invalid Home Assistant platform value [{input_item['home_assistant']['platform']}] for entry [{input_item['name']}]: only the 'binary_sensor' platform is supported for now."
                    )
                if (
                    input_item["home_assistant"]["device_class"]
                    not in HomeAssistantDefaults.ALLOWED_DEVICE_CLASSES["binary_sensor"]
                ):
                    raise ValueError(
                        f"Invalid Home Assistant device_class value [{input_item['home_assistant']['device_class']}] for entry [{input_item['name']}]: the allowed values are: {HomeAssistantDefaults.ALLOWED_DEVICE_CLASSES['binary_sensor']}."
                    )

                # store as valid entry
                self.optoisolated_inputs_map[idx] = input_item
                # print(input_item)
            print(f"Loaded {len(self.optoisolated_inputs_map)} opto-isolated input configurations")
            if len(self.optoisolated_inputs_map) == 0:
                # reset to "not loaded at all" condition
                self.optoisolated_inputs_map = None
        except ValueError as e:
            print(f"Error in YAML config file '{cfg_yaml}': {e}")
            return False
        except KeyError as e:
            print(f"Error in YAML config file '{cfg_yaml}': {e} is missing")
            return False

        try:
            # convert the 'gpio_inputs' part in a dictionary indexed by the GPIO PIN NUMBER:
            self.gpio_inputs_map = {}

            if "gpio_inputs" not in self.config:
                # empty list: feature disabled
                self.config["gpio_inputs"] = []

            for input_item in self.config["gpio_inputs"]:
                input_item = self.populate_defaults_in_list_entry(
                    input_item,
                    populate_homeassistant=False,
                    has_payload_on_off=False,
                    has_state_topic=False,
                    is_output=False,
                    populate_filter=False,  # GPIO inputs do not support filtering at this time
                )

                # check GPIO index
                idx = int(input_item["gpio"])
                self.check_gpio(idx)
                if idx in self.gpio_inputs_map:
                    raise ValueError(
                        f"Invalid gpio index {idx} for entry [{input_item['name']}]: such GPIO input has already been used. Check again the configuration."
                    )

                # store as valid entry
                self.gpio_inputs_map[idx] = input_item
                # print(input_item)
            print(f"Loaded {len(self.gpio_inputs_map)} GPIO input configurations")
            if len(self.gpio_inputs_map) == 0:
                # reset to "not loaded at all" condition
                self.gpio_inputs_map = None
        except ValueError as e:
            print(f"Error in YAML config file '{cfg_yaml}': {e}")
            return False
        except KeyError as e:
            print(f"Error in YAML config file '{cfg_yaml}': {e} is missing")
            return False

        try:
            # convert the 'outputs' part in a dictionary indexed by the MQTT TOPIC:
            self.outputs_map = {}

            if "outputs" not in self.config:
                # empty list: feature disabled
                self.config["outputs"] = []

            for output_item in self.config["outputs"]:

                output_item = self.populate_defaults_in_list_entry(
                    output_item,
                    is_output=True,
                    populate_filter=False,  # filtering does not make sense for outputs
                )

                # check GPIO index
                idx = int(output_item["gpio"])
                self.check_gpio(idx)

                mqtt_topic = output_item["mqtt"]["topic"]
                if mqtt_topic in self.outputs_map:
                    raise ValueError(
                        f"Invalid MQTT topic [{mqtt_topic}] for entry [{output_item['name']}]: such MQTT topic has already been used. Check again the configuration."
                    )

                # check HomeAssistant section
                if output_item["home_assistant"]["platform"] not in ["switch", "button"]:
                    raise ValueError(
                        f"Invalid Home Assistant platform value [{output_item['home_assistant']['platform']}] for entry [{output_item['name']}]: only the 'switch' or 'button' platforms are supported for now."
                    )

                allowed_dev_classes = HomeAssistantDefaults.ALLOWED_DEVICE_CLASSES[
                    output_item["home_assistant"]["platform"]
                ]
                if output_item["home_assistant"]["device_class"] not in allowed_dev_classes:
                    raise ValueError(
                        f"Invalid Home Assistant device_class value [{output_item['home_assistant']['device_class']}] for entry [{output_item['name']}]: the allowed values are: {allowed_dev_classes}."
                    )

                # store as valid entry
                self.outputs_map[mqtt_topic] = output_item
                # print(output_item)
            print(f"Loaded {len(self.outputs_map)} digital output configurations")
            if len(self.outputs_map) == 0:
                # reset to "not loaded at all" condition
                self.outputs_map = None
        except ValueError as e:
            print(f"Error in YAML config file '{cfg_yaml}': {e}")
            return False
        except KeyError as e:
            print(f"Error in YAML config file '{cfg_yaml}': {e} is missing")
            return False

        # validate that there is no duplicated 'name' across all configuration entries
        name_set = set()
        merged_entries_list = []
        if self.optoisolated_inputs_map is not None:
            merged_entries_list = merged_entries_list + list(self.optoisolated_inputs_map.values())
        if self.gpio_inputs_map is not None:
            merged_entries_list = merged_entries_list + list(self.gpio_inputs_map.values())
        if self.outputs_map is not None:
            merged_entries_list = merged_entries_list + list(self.outputs_map.values())
        for entry in merged_entries_list:
            if entry["name"] in name_set:
                print(
                    f"Error in YAML config file '{cfg_yaml}': the name {entry['name']} is not unique across the configuration."
                )
            else:
                name_set.add(entry["name"])

        print("Successfully loaded configuration")
        return True

    def merge_options_from_cli(self, args: dict):
        # merge CLI options into the configuration object:
        self.disable_hw = args.disable_hw
        self.verbose = args.verbose

    def merge_options_from_env_vars(self):
        # merge env vars into the configuration object:
        if os.environ.get("DISABLE_HW", None) is not None:
            self.disable_hw = True
        if os.environ.get("VERBOSE", None) is not None:
            self.verbose = True
        if os.environ.get("MQTT_BROKER_HOST", None) is not None:
            # this particular env var can override the value coming from the config file:
            self.mqtt_broker_host = os.environ.get("MQTT_BROKER_HOST")
        if os.environ.get("MQTT_BROKER_PORT", None) is not None:
            # this particular env var can override the value coming from the config file:
            self.mqtt_broker_port = os.environ.get("MQTT_BROKER_PORT")

    def print_config_summary(self):
        print("Config summary:")
        print("** MQTT")
        print(f"   MQTT broker host:port: {self.mqtt_broker_host}:{self.mqtt_broker_port}")
        if self.mqtt_broker_user is not None:
            print("   MQTT broker authentication: ON")
        else:
            print("   MQTT broker authentication: OFF")
        print(f"   MQTT reconnection period: {self.mqtt_reconnection_period_sec}s")
        print("** HomeAssistant")
        print(f"   MQTT publish period: {self.homeassistant_publish_period_sec}s")
        print(f"   Discovery messages: {self.homeassistant_discovery_messages_enable}")
        print(f"   Node ID: {self.homeassistant_discovery_topic_node_id}")
        print("** I2C isolated inputs:")
        if self.optoisolated_inputs_map is not None:
            for k, v in self.optoisolated_inputs_map.items():
                print(f"   input#{k}: {v['name']}")
        print("** GPIO inputs:")
        if self.gpio_inputs_map is not None:
            for k, v in self.gpio_inputs_map.items():
                print(f"   input#{k}: {v['name']}")
        print("** OUTPUTs:")
        i = 1
        if self.outputs_map is not None:
            for k, v in self.outputs_map.items():
                print(f"   output#{i}: {v['name']}")
            i += 1
        print("** MISC:")
        print(f"   Log stats every: {self.stats_log_period_sec}s")

    # MQTT

    @property
    def mqtt_broker_host(self) -> str:
        if self.config is None:
            return ""  # no meaningful default value
        return self.config["mqtt_broker"]["host"]

    @mqtt_broker_host.setter
    def mqtt_broker_host(self, value):
        self.config["mqtt_broker"]["host"] = value

    @property
    def mqtt_broker_user(self) -> str:
        if self.config is None:
            return None  # default is unauthenticated
        if "user" not in self.config["mqtt_broker"]:
            return None  # default is unauthenticated
        return self.config["mqtt_broker"]["user"]

    @property
    def mqtt_broker_password(self) -> str:
        if self.config is None:
            return None  # default is unauthenticated
        if "password" not in self.config["mqtt_broker"]:
            return None  # default is unauthenticated
        return self.config["mqtt_broker"]["password"]

    @property
    def mqtt_broker_port(self) -> int:
        if self.config is None:
            return MqttDefaults.BROKER_PORT  # the default MQTT broker port
        if "port" not in self.config["mqtt_broker"]:
            return MqttDefaults.BROKER_PORT  # the default MQTT broker port
        return self.config["mqtt_broker"]["port"]

    @mqtt_broker_port.setter
    def mqtt_broker_port(self, value):
        self.config["mqtt_broker"]["port"] = int(value)

    @property
    def mqtt_reconnection_period_sec(self) -> float:
        if self.config is None:
            return MqttDefaults.RECONNECTION_PERIOD_SEC  # the default reconnection interval

        try:
            # convert the user-defined timeout from msec to (floating) sec
            cfg_value = float(self.config["mqtt_broker"]["reconnection_period_msec"]) / 1000.0
            return cfg_value
        except (KeyError, ValueError):
            # in this case the key is completely missing or does contain an integer value
            return MqttDefaults.RECONNECTION_PERIOD_SEC  # default value

    #
    # HOME-ASSISTANT
    #

    @property
    def homeassistant_publish_period_sec(self) -> float:
        if self.config is None:
            return HomeAssistantDefaults.PUBLISH_PERIOD_SEC  # default value
        try:
            cfg_value = float(self.config["home_assistant"]["publish_period_msec"]) / 1000.0
            return cfg_value
        except (KeyError, ValueError):
            # in this case the key is completely missing or does contain an integer value
            return HomeAssistantDefaults.PUBLISH_PERIOD_SEC  # default value

    @property
    def homeassistant_default_topic_prefix(self) -> str:
        if self.config is None:
            return HomeAssistantDefaults.TOPIC_PREFIX  # default value
        try:
            return self.config["home_assistant"]["default_topic_prefix"]
        except KeyError:
            # in this case the key is completely missing or does contain an integer value
            return HomeAssistantDefaults.TOPIC_PREFIX  # default value

    @property
    def homeassistant_discovery_messages_enable(self) -> bool:
        if self.config is None:
            return True  # default value
        try:
            return self.config["home_assistant"]["discovery_messages"]["enable"]
        except KeyError:
            # in this case the key is completely missing or does contain an integer value
            return True  # default value

    @property
    def homeassistant_discovery_topic_prefix(self) -> str:
        if self.config is None:
            return HomeAssistantDefaults.DISCOVERY_TOPIC_PREFIX  # default value
        try:
            return self.config["home_assistant"]["discovery_messages"]["topic_prefix"]
        except KeyError:
            # in this case the key is completely missing or does contain an integer value
            return HomeAssistantDefaults.DISCOVERY_TOPIC_PREFIX  # default value

    @property
    def homeassistant_discovery_topic_node_id(self) -> str:
        if self.config is None:
            return self.current_hostname  # default value
        try:
            return self.config["home_assistant"]["discovery_messages"]["node_id"]
        except KeyError:
            # in this case the key is completely missing or does contain an integer value
            return self.current_hostname  # default value

    #
    # MISC
    #

    @property
    def stats_log_period_sec(self) -> int:
        if self.config is None or "log_stats_every" not in self.config:
            return MiscAppDefaults.STATS_LOG_PERIOD_SEC  # default value
        return int(self.config["log_stats_every"])

    #
    # OPTO-ISOLATED INPUTS
    #

    def get_optoisolated_input_config(self, index: int) -> dict[str, any]:
        """
        Returns a dictionary containing all the possible keys that are
        valid for an opto-isolated input config (see the SCHEMA in the load() API),
        including optional keys that were not given in the YAML.
        """
        if self.optoisolated_inputs_map is None or index not in self.optoisolated_inputs_map:
            return None  # no meaningful default value
        return self.optoisolated_inputs_map[index]

    def get_all_optoisolated_inputs(self) -> list:
        """
        Returns a list of dictionaries with configurations
        """
        if "i2c_optoisolated_inputs" not in self.config:
            return None  # no meaningful default value
        return self.config["i2c_optoisolated_inputs"]

    #
    # GPIO INPUTS
    #

    def get_gpio_input_config(self, index: int) -> dict[str, any]:
        """
        Returns a dictionary containing all the possible keys that are
        valid for a GPIO input config (see the SCHEMA in the load() API),
        including optional keys that were not given in the YAML.
        """
        if self.gpio_inputs_map is None or index not in self.gpio_inputs_map:
            return None  # no meaningful default value
        return self.gpio_inputs_map[index]

    def get_all_gpio_inputs(self) -> list:
        """
        Returns a list of dictionaries with configurations
        """
        if "gpio_inputs" not in self.config:
            return None  # no meaningful default value
        return self.config["gpio_inputs"]

    #
    # OUTPUTS CONFIG
    #

    def get_output_config_by_mqtt_topic(self, topic: str) -> dict[str, any]:
        """
        Returns a dictionary containing all the possible keys that are
        valid for a GPIO output config (see the SCHEMA in the load() API),
        including optional keys that were not given in the YAML.
        """
        if self.outputs_map is None or topic not in self.outputs_map:
            return None  # no meaningful default value
        return self.outputs_map[topic]

    def get_all_outputs(self) -> list:
        """
        Returns a list of dictionaries with configurations
        """
        if "outputs" not in self.config:
            return None  # no meaningful default value
        return self.config["outputs"]

    #
    # MQTT HELPERs
    #
    def create_aiomqtt_client(self, identifier_str: str) -> aiomqtt.Client:
        """
        Creates an aiomqtt client based on the configuration information provided to this app.
        The 'identifier_str' can be used to uniquely name the client connection.
        Such unique name appears in MQTT broker logs and is useful for debug.
        """
        return aiomqtt.Client(
            hostname=self.mqtt_broker_host,
            port=self.mqtt_broker_port,
            timeout=self.mqtt_reconnection_period_sec,
            username=self.mqtt_broker_user,
            password=self.mqtt_broker_password,
            identifier=self.mqtt_identifier_prefix + identifier_str,
        )

    def get_device_dict(self) -> dict:
        return {
            "manufacturer": HomeAssistantDefaults.MANUFACTURER,
            "model": MiscAppDefaults.THIS_APP_NAME,
            # rationale for having "device name == MQTT node_id":
            # a) in the unlikely event that you have more than 1 raspberry running this software
            #    you likely have different hostnames on them and node_id defaults to the hostname
            # b) node_id is configurable via config file
            "name": self.homeassistant_discovery_topic_node_id,
            "sw_version": self.app_version,
            "identifiers": [f"{MiscAppDefaults.THIS_APP_NAME}-{self.homeassistant_discovery_topic_node_id}"],
        }
