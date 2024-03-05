#!/usr/bin/env python3

#
# Author: fmontorsi
# Created: Feb 2024
# License: Apache license
#
# TODO: add prometheus support to monitor MQTT update timings (latencies) to help debug "sensor unavailable" on HA
#       see https://prometheus.github.io/client_python/getting-started/three-step-demo/

import argparse
import os
import fcntl
import sys
import yaml
import asyncio
import aiomqtt
import lib16inpind
import gpiozero
import subprocess
import time
import threading
import queue
import json

# =======================================================================================================
# GLOBALs
# =======================================================================================================

THIS_SCRIPT_PYPI_PACKAGE = "ha-alarm-raspy2mqtt"
MQTT_TOPIC_PREFIX = "home"
MQTT_QOS_AT_LEAST_ONCE = 1
BROKER_CONNECTION_TIMEOUT_SEC = 3

# SequentMicrosystem-specific constants
SEQMICRO_INPUTHAT_STACK_LEVEL = 0 # 0 means the first "stacked" board (this code supports only 1!)
SEQMICRO_INPUTHAT_MAX_CHANNELS = 16
SEQMICRO_INPUTHAT_SHUTDOWN_BUTTON_GPIO = 26 # GPIO pin connected to the push button
SEQMICRO_INPUTHAT_INTERRUPT_GPIO = 11 # GPIO pin connected to the interrupt line of the I/O expander (need pullup resistor)
SEQMICRO_INPUTHAT_I2C_SDA = 2 # reserved for I2C communication between Raspberry CPU and the input HAT
SEQMICRO_INPUTHAT_I2C_SCL = 3 # reserved for I2C communication between Raspberry CPU and the input HAT

# global stat dictionary
g_stats = {
    'optoisolated_inputs': {
        'num_readings': 0,
        'num_connections_publish': 0,
        'num_mqtt_messages': 0,
    },
    'gpio_inputs': {
        'num_connections_publish': 0,
        'num_gpio_notifications': 0,
        'num_mqtt_messages': 0,
    },
    'outputs': {
        'num_connections_subscribe': 0,
        'num_mqtt_commands_processed': 0,
        'num_connections_publish': 0,
        'num_mqtt_states_published': 0,
    },
    'num_connections_lost': 0
}

# global dictionary of gpiozero.LED instances used to drive outputs
g_output_channels = {}

# thread-safe queue to communicate from GPIOzero secondary threads to main thread
g_gpio_queue = queue.Queue()

# global start time
g_start_time = time.time()

# last reading of the 16 digital opto-isolated inputs 
g_optoisolated_inputs_sampled_values = 0

# =======================================================================================================
# CfgFile
# =======================================================================================================

class CfgFile:
    """
    This class represents the YAML config file for this utility
    """

    def __init__(self):
        self.config: Optional[Dict[str, Any]] = None
        self.optoisolated_inputs_map: Optional[Dict[int, Any]] = None # None means "not loaded at all"
    
    def load(self, cfg_yaml: str) -> bool:
        print(f"Loading configuration file {cfg_yaml}")
        try:
            with open(cfg_yaml, 'r') as file:
                self.config = yaml.safe_load(file)
            if not isinstance(self.config, dict):
                raise ValueError("Invalid YAML format: root element must be a dictionary")
            if 'mqtt_broker' not in self.config:
                raise ValueError("Missing 'mqtt_broker' section in the YAML config file")
            if 'host' not in self.config['mqtt_broker']:
                raise ValueError("Missing 'mqtt_broker.host' field in the YAML config file")
            if 'i2c_optoisolated_inputs' not in self.config:
                raise ValueError("Missing 'i2c_optoisolated_inputs' section in the YAML config file")
            if self.config['i2c_optoisolated_inputs'] is None:
                raise ValueError("Missing 'i2c_optoisolated_inputs' section in the YAML config file")
            if 'outputs' not in self.config:
                raise ValueError("Missing 'outputs' section in the YAML config file")
            if self.config['outputs'] is None:
                raise ValueError("Missing 'outputs' section in the YAML config file")
        except FileNotFoundError:
            print(f"Error: configuration file '{cfg_yaml}' not found.")
            return False
        except yaml.YAMLError as e:
            print(f"Error parsing YAML config file '{cfg_yaml}': {e}")
            return False
        except ValueError as e:
            print(f"Error in YAML config file '{cfg_yaml}': {e}")
            return False

        try:
            # convert the 'i2c_optoisolated_inputs' part in a dictionary indexed by the DIGITAL INPUT CHANNEL NUMBER:
            self.optoisolated_inputs_map = {}
            for input_item in self.config['i2c_optoisolated_inputs']:
                idx = int(input_item['input_num'])
                if idx < 1 or idx > 16:
                    raise ValueError(f"Invalid input_num {idx}. The legal range is [1-16] since the Sequent Microsystem HAT only handles 16 inputs.")
                self.optoisolated_inputs_map[idx] = input_item
                #print(input_item)
            print(f"Loaded {len(self.optoisolated_inputs_map)} opto-isolated input configurations")
            if len(self.optoisolated_inputs_map)==0:
                # reset to "not loaded at all" condition
                self.optoisolated_inputs_map = None
        except ValueError as e:
            print(f"Error in YAML config file '{cfg_yaml}': {e}")
            return False
        except KeyError as e:
            print(f"Error in YAML config file '{cfg_yaml}': {e} is missing")
            return False

        reserved_gpios = [ SEQMICRO_INPUTHAT_SHUTDOWN_BUTTON_GPIO, SEQMICRO_INPUTHAT_INTERRUPT_GPIO, SEQMICRO_INPUTHAT_I2C_SDA, SEQMICRO_INPUTHAT_I2C_SCL ]

        try:
            # convert the 'gpio_inputs' part in a dictionary indexed by the GPIO PIN NUMBER:
            self.gpio_inputs_map = {}
            for input_item in self.config['gpio_inputs']:
                idx = int(input_item['gpio'])
                if idx < 1 or idx > 40:
                    raise ValueError(f"Invalid input_num {idx}. The legal range is [1-40] since the Raspberry GPIO connector is a 40-pin connector.")
                # some GPIO pins are reserved and cannot be configured!
                if idx in reserved_gpios:
                    raise ValueError(f"Invalid input_num {idx}: that GPIO pin is reserved for communication with the Sequent Microsystem HAT. Choose a different GPIO.")
                self.gpio_inputs_map[idx] = input_item
                #print(input_item)
            print(f"Loaded {len(self.gpio_inputs_map)} GPIO input configurations")
            if len(self.gpio_inputs_map)==0:
                # reset to "not loaded at all" condition
                self.gpio_inputs_map = None
        except ValueError as e:
            print(f"Error in YAML config file '{cfg_yaml}': {e}")
            return False
        except KeyError as e:
            print(f"Error in YAML config file '{cfg_yaml}': {e} is missing")
            return False
        
        try:
            # convert the 'outputs' part in a dictionary indexed by the NAME:
            self.outputs_map = {}
            for output_item in self.config['outputs']:
                self.outputs_map[output_item['name']] = output_item
                #print(output_item)
            print(f"Loaded {len(self.outputs_map)} digital output configurations")
            if len(self.outputs_map)==0:
                # reset to "not loaded at all" condition
                self.outputs_map = None
        except ValueError as e:
            print(f"Error in YAML config file '{cfg_yaml}': {e}")
            return False
        except KeyError as e:
            print(f"Error in YAML config file '{cfg_yaml}': {e} is missing")
            return False

        print(f"Successfully loaded configuration")

        return True

    @property
    def mqtt_broker_host(self) -> str:
        if self.config is None:
            return '' # no meaningful default value
        return self.config['mqtt_broker']['host']
    @property
    def mqtt_broker_port(self) -> int:
        if self.config is None:
            return 1883 # the default MQTT broker port
        if 'port' not in self.config['mqtt_broker']:
            return 1883 # the default MQTT broker port
        return self.config['mqtt_broker']['port']

    @property
    def sampling_frequency_sec(self) -> float:
        if self.config is None:
            return 1.0 # default value
        try:
            cfg_value = float(self.config['sampling_frequency_msec'])/1000.0
            return cfg_value
        except:
            # in this case the key is completely missing or does contain an integer value
            return 1.0 # default value
        
    @property
    def stats_log_period_sec(self) -> int:
        if self.config is None or 'log_stats_every' not in self.config:
            return 30 # default value
        return int(self.config['log_stats_every'])

    #
    # OPTO-ISOLATED INPUTS
    #

    def get_optoisolated_input_config(self, index: int) -> dict[str, any]:
        """
        Returns a dictionary exposing the fields:
            'name': name of the digital input
            'active_low': True or False
        Note: the indexes are 1-based
        """
        if self.optoisolated_inputs_map is None or index not in self.optoisolated_inputs_map:
            return None # no meaningful default value
        return self.optoisolated_inputs_map[index]
    

    #
    # GPIO INPUTS
    #

    def get_all_gpio_inputs(self):
        """
        Returns a list of dictionaries exposing the fields:
             'name': name of the digital input
             'gpio': integer identifying the GPIO pin using Raspy standard 40pin naming
             'active_low': True or False
        """
        if 'gpio_inputs' not in self.config:
            return None # no meaningful default value
        return self.config['gpio_inputs']

    def get_gpio_input_config(self, index: int) -> dict[str, any]:
        """
        Returns a dictionary exposing the fields:
            'name': name of the digital input
            'active_low': True or False
            'mqtt': a dictionary with more details about the TOPIC and PAYLOAD to send on input activation (see config.yaml)
        """
        if self.gpio_inputs_map is None or index not in self.gpio_inputs_map:
            return None # no meaningful default value
        return self.gpio_inputs_map[index]

    #
    # OUTPUTS CONFIG
    #

    def get_output_config(self, name: str) -> dict[str, any]:
        """
        Returns a dictionary exposing the fields:
            'name': name of the digital output
            'gpio': integer identifying the GPIO pin using Raspy standard 40pin naming
            'active_low': True or False
        """
        if self.outputs_map is None or name not in self.outputs_map:
            return None # no meaningful default value
        return self.outputs_map[name]

    def get_all_outputs(self):
        """
        Returns a list of dictionaries exposing the fields:
             'name': name of the digital output
             'gpio': integer identifying the GPIO pin using Raspy standard 40pin naming
             'active_low': True or False
        """
        if 'outputs' not in self.config:
            return None # no meaningful default value
        return self.config['outputs']

# =======================================================================================================
# MAIN HELPERS
# =======================================================================================================

def parse_command_line():
    """Parses the command line and returns the configuration as dictionary object."""
    parser = argparse.ArgumentParser(
        description=f"Utility to expose the {SEQMICRO_INPUTHAT_MAX_CHANNELS} digital inputs read by Raspberry over MQTT, to ease their integration as (binary) sensors in Home Assistant."
    )

    # Optional arguments
    # NOTE: we cannot add required=True to --output option otherwise it's impossible to invoke this tool with just --version
    parser.add_argument(
        "-c",
        "--config",
        help="YAML file specifying the software configuration. Defaults to 'config.yaml'",
        default='config.yaml',
    )
    parser.add_argument(
        "-v", "--verbose", help="Be verbose.", action="store_true", default=False
    )
    parser.add_argument(
        "-V",
        "--version",
        help="Print version and exit",
        action="store_true",
        default=False,
    )

    if "COLUMNS" not in os.environ:
        os.environ["COLUMNS"] = "120"  # avoid too many line wraps
    args = parser.parse_args()

    global verbose
    verbose = args.verbose

    if args.version:
        try:
            from importlib.metadata import version
        except:
            from importlib_metadata import version
        this_script_version = version(THIS_SCRIPT_PYPI_PACKAGE)
        print(f"Version: {this_script_version}")
        sys.exit(0)

    return args

def instance_already_running(label="default"):
    """
    Detect if an an instance with the label is already running, globally
    at the operating system level.

    Using `os.open` ensures that the file pointer won't be closed
    by Python's garbage collector after the function's scope is exited.

    The lock will be released when the program exits, or could be
    released if the file pointer were closed.
    """

    lock_file_pointer = os.open(f"/tmp/instance_{label}.lock", os.O_WRONLY | os.O_CREAT)

    try:
        # LOCK_NB = lock non-blocking
        # LOCK_EX = exclusive lock
        fcntl.lockf(lock_file_pointer, fcntl.LOCK_EX | fcntl.LOCK_NB)
        already_running = False
    except IOError:
        already_running = True

    return already_running

def print_stats():
    global g_stats, g_start_time
    print_stats.counter = getattr(print_stats, 'counter', 0) + 1
    print(f">> STAT REPORT #{print_stats.counter}")

    uptime_sec = time.time() - g_start_time
    m, s = divmod(uptime_sec, 60)
    h, m = divmod(m, 60)
    h = int(h)
    m = int(m)
    s = int(s)
    print(f">> Uptime: {h}:{m:02}:{s:02}")
    print(f">> Num times the MQTT broker connection was lost: {g_stats['num_connections_lost']}")

    x = g_stats["optoisolated_inputs"]
    print(f">> OPTO-ISOLATED INPUTS:")
    print(f">>   Num (re)connections to the MQTT broker [publish channel]: {x['num_connections_publish']}")
    print(f">>   Num MQTT messages published to the broker: {x['num_mqtt_messages']}")
    print(f">>   Num actual readings of optoisolated inputs: {x['num_readings']}")

    x = g_stats["gpio_inputs"]
    print(f">> GPIO INPUTS:")
    print(f">>   Num (re)connections to the MQTT broker [publish channel]: {x['num_connections_publish']}")
    print(f">>   Num GPIO activations detected: {x['num_gpio_notifications']}")
    print(f">>   Num MQTT messages published to the broker: {x['num_mqtt_messages']}")

    x = g_stats["outputs"]
    print(f">> OUTPUTS:")
    print(f">>   Num (re)connections to the MQTT broker [subscribe channel]: {x['num_connections_subscribe']}")
    print(f">>   Num commands for output channels processed from MQTT broker: {x['num_mqtt_commands_processed']}")
    print(f">>   Num (re)connections to the MQTT broker [publish channel]: {x['num_connections_publish']}")
    print(f">>   Num states for output channels published on the MQTT broker: {x['num_mqtt_states_published']}")


# =======================================================================================================
# GPIOZERO helper functions
# These functions execute in the context of secondary threads created by gpiozero library
# and attached to INPUT button pressure
# =======================================================================================================

def shutdown():
    print(f"!! Detected long-press on the Sequent Microsystem button. Triggering clean shutdown of the Raspberry PI !!")
    subprocess.call(['sudo', 'shutdown', '-h', 'now'])

def sample_optoisolated_inputs():
    global g_stats, g_optoisolated_inputs_sampled_values
    # Read 16 digital inputs
    # NOTE1: this is a blocking call that will block until the 16 inputs are sampled
    # NOTE2: this might raise a TimeoutError exception in case the I2C bus transaction fails
    g_optoisolated_inputs_sampled_values = lib16inpind.readAll(SEQMICRO_INPUTHAT_STACK_LEVEL)
    g_stats["optoisolated_inputs"]["num_readings"] += 1

def publish_mqtt_message(device):
    print(f"!! Detected activation of GPIO{device.pin.number} !! ")
    g_gpio_queue.put(device.pin.number)


# =======================================================================================================
# ASYNC HELPERS
# =======================================================================================================

async def print_stats_periodically(cfg: CfgFile):
    loop = asyncio.get_running_loop()
    next_stat_time = loop.time() + cfg.stats_log_period_sec
    while True:
        # Print out stats to help debugging
        if loop.time() >= next_stat_time:
            print_stats()
            next_stat_time = loop.time() + cfg.stats_log_period_sec
        
        await asyncio.sleep(1)

async def publish_optoisolated_inputs(cfg: CfgFile):
    """
    This function may throw a aiomqtt.MqttError exception indicating a connection issue!
    """
    global g_stats, g_optoisolated_inputs_sampled_values

    print(f"Connecting to MQTT broker at address {cfg.mqtt_broker_host}:{cfg.mqtt_broker_port} to publish OPTOISOLATED INPUT states")
    g_stats["optoisolated_inputs"]['num_connections_publish'] += 1
    async with aiomqtt.Client(cfg.mqtt_broker_host, port=cfg.mqtt_broker_port, timeout=BROKER_CONNECTION_TIMEOUT_SEC) as client:
        while True:
            # Publish each input value as a separate MQTT topic
            update_loop_start_sec = time.perf_counter()
            for i in range(SEQMICRO_INPUTHAT_MAX_CHANNELS):
                # Extract the bit at position i using bitwise AND operation
                bit_value = bool(g_optoisolated_inputs_sampled_values & (1 << i))

                input_cfg = cfg.get_optoisolated_input_config(1 + i)  # convert from zero-based index 'i' to 1-based index
                if input_cfg is not None:
                    # Choose the TOPIC and message PAYLOAD
                    topic = f"{MQTT_TOPIC_PREFIX}/{input_cfg['name']}"
                    if input_cfg['active_low']:
                        logical_value = not bit_value
                        input_type = 'active low'
                    else:
                        logical_value = bit_value
                        input_type = 'active high'

                    payload = "ON" if logical_value else "OFF"
                    #print(f"From INPUT#{i+1} [{input_type}] read {int(bit_value)} -> {int(logical_value)}; publishing on mqtt topic [{topic}] the payload: {payload}")

                    await client.publish(topic, payload, qos=MQTT_QOS_AT_LEAST_ONCE)
                    g_stats["optoisolated_inputs"]["num_mqtt_messages"] += 1

            update_loop_duration_sec = time.perf_counter() - update_loop_start_sec
            #print(f"Updating all sensors on MQTT took {update_loop_duration_sec} secs")

            # Now sleep a little bit before repeating
            await asyncio.sleep(cfg.sampling_frequency_sec - update_loop_duration_sec)

async def process_gpio_inputs_queue_and_publish(cfg: CfgFile):
    """
    This function may throw a aiomqtt.MqttError exception indicating a connection issue!
    """
    global g_stats

    print(f"Connecting to MQTT broker at address {cfg.mqtt_broker_host}:{cfg.mqtt_broker_port} to publish GPIO INPUT states")
    g_stats["gpio_inputs"]["num_connections_publish"] += 1
    async with aiomqtt.Client(cfg.mqtt_broker_host, port=cfg.mqtt_broker_port, timeout=BROKER_CONNECTION_TIMEOUT_SEC) as client:
        while True:
            # get next notification coming from the gpiozero secondary thread:
            try:
                gpio_number = g_gpio_queue.get_nowait()
            except queue.Empty:
                # if there's no notification (typical case), then do not block the event loop
                # and keep processing other tasks... to ensure low-latency in processing the
                # GPIO inputs the sleep time is set equal to the opto-isolated input sampling freq
                await asyncio.sleep(cfg.sampling_frequency_sec)
                continue
        
            # there is a GPIO notification to process:
            gpio_config = cfg.get_gpio_input_config(gpio_number)
            g_stats["gpio_inputs"]["num_gpio_notifications"] += 1
            if gpio_config is None or 'mqtt' not in gpio_config:
                print(f'Main thread got notification of GPIO#{gpio_number} being activated but there is NO CONFIGURATION for that pin. Ignoring.')
            else:
                # extract MQTT config
                mqtt_topic = gpio_config['mqtt']['topic']
                mqtt_command = gpio_config['mqtt']['command']
                mqtt_code = gpio_config['mqtt']['code']
                print(f'Main thread got notification of GPIO#{gpio_number} being activated; a valid MQTT configuration is attached: topic={mqtt_topic}, command={mqtt_command}, code={mqtt_code}')

                # now launch the MQTT publish
                #mqtt_payload = {
                #    "command": mqtt_command,
                #    "code": mqtt_code
                #}
                #mqtt_payload_str = json.dumps(mqtt_payload)
                mqtt_payload_str = mqtt_command
                await client.publish(mqtt_topic, mqtt_payload_str, qos=MQTT_QOS_AT_LEAST_ONCE)
                print(f'Sent on topic={mqtt_topic}, payload={mqtt_payload_str}')
                g_stats["gpio_inputs"]["num_mqtt_messages"] += 1

            g_gpio_queue.task_done()


async def subscribe_and_activate_outputs(cfg: CfgFile):
    """
    This function may throw a aiomqtt.MqttError exception indicating a connection issue!
    """
    global g_stats
    global g_output_channels

    print(f"Connecting to MQTT broker at address {cfg.mqtt_broker_host}:{cfg.mqtt_broker_port} to subscribe to OUTPUT commands")
    g_stats["outputs"]["num_connections_subscribe"] += 1
    async with aiomqtt.Client(cfg.mqtt_broker_host, port=cfg.mqtt_broker_port, timeout=BROKER_CONNECTION_TIMEOUT_SEC) as client:
        for output_ch in cfg.get_all_outputs():
            topic = f"{MQTT_TOPIC_PREFIX}/{output_ch['name']}"
            print(f"Subscribing to topic {topic}")
            await client.subscribe(topic)

        async for message in client.messages:
            output_name = str(message.topic).removeprefix(f"{MQTT_TOPIC_PREFIX}/")
            c = cfg.get_output_config(output_name)
            print(f"Received message for digital output [{output_name}] with payload {message.payload}... changing GPIO output pin state")
            if message.payload == b'ON':
                g_output_channels[output_name].on()
            else:
                g_output_channels[output_name].off()
            g_stats["outputs"]['num_mqtt_commands_processed'] += 1

async def publish_outputs_state(cfg: CfgFile):
    """
    This function may throw a aiomqtt.MqttError exception indicating a connection issue!
    """
    global g_stats
    global g_output_channels

    print(f"Connecting to MQTT broker at address {cfg.mqtt_broker_host}:{cfg.mqtt_broker_port} to publish OUTPUT states")
    g_stats["outputs"]["num_connections_publish"] += 1
    output_status_map = {}
    async with aiomqtt.Client(cfg.mqtt_broker_host, port=cfg.mqtt_broker_port, timeout=BROKER_CONNECTION_TIMEOUT_SEC) as client:
        while True:
            for output_ch in cfg.get_all_outputs():
                output_name = output_ch['name']
                output_status = g_output_channels[output_name].is_lit

                if output_name not in output_status_map or output_status_map[output_name] != output_status:
                    # need to publish an update over MQTT... the state has changed
                    topic = f"{MQTT_TOPIC_PREFIX}/{output_name}/state"
                    payload = "ON" if output_status else "OFF"

                    # publish with RETAIN flag so that Home Assistant will always find an updated status on
                    # the broker about each switch
                    #print(f"Publishing to topic {topic} the payload {payload}")
                    await client.publish(topic, payload, qos=MQTT_QOS_AT_LEAST_ONCE, retain=True)
                    g_stats["outputs"]['num_mqtt_states_published'] += 1

                    # remember the status we just published in order to later skip meaningless updates
                    # when there is no state change:
                    output_status_map[output_name] = output_status

            await asyncio.sleep(cfg.sampling_frequency_sec)

async def main_loop():
    global g_stats

    args = parse_command_line()
    cfg = CfgFile()
    if not cfg.load(args.config):
        return 1 # invalid config file... abort with failure exit code
    
    # check if the opto-isolated input board from Sequent Microsystem is indeed present:
    try:
        _ = lib16inpind.readAll(SEQMICRO_INPUTHAT_STACK_LEVEL)
    except FileNotFoundError as e:
        print(f"Could not read from the Sequent Microsystem opto-isolated input board: {e}. Aborting.")
        return 2
    except OSError as e:
        print(f"Error while reading from the Sequent Microsystem opto-isolated input board: {e}. Aborting.")
        return 2
    except BaseException as e:
        print(f"Error while reading from the Sequent Microsystem opto-isolated input board: {e}. Aborting.")
        return 2

    # setup GPIO connected to the pushbutton (input) and
    # assign the when_held function to be called when the button is held for more than 5 seconds
    # (NOTE: the way gpiozero works is that a new thread is spawned to listed for this event on the Raspy GPIO)
    buttons = []
    print(f"Initializing SequentMicrosystem GPIO shutdown button")
    b = gpiozero.Button(SEQMICRO_INPUTHAT_SHUTDOWN_BUTTON_GPIO, hold_time=5)
    b.when_held = shutdown
    buttons.append(b)

    print(f"Initializing SequentMicrosystem GPIO interrupt line")
    b = gpiozero.Button(SEQMICRO_INPUTHAT_INTERRUPT_GPIO, pullup=True)
    b.when_held = sample_optoisolated_inputs
    buttons.append(b)

    # setup GPIO pins for the INPUTs
    print(f"Initializing GPIO input pins")
    for input_ch in cfg.get_all_gpio_inputs():
        # the short hold-time is to ensure that the digital input is served ASAP (i.e. publish_mqtt_message gets
        # invoked almost immediately)
        active_high = not bool(input_ch['active_low'])
        b = gpiozero.Button(input_ch['gpio'], hold_time=0.1, pull_up=None, active_state=active_high)
        b.when_held = publish_mqtt_message
        buttons.append(b)

    # setup GPIO pins for the OUTPUTs
    print(f"Initializing GPIO output pins")
    global g_output_channels
    for output_ch in cfg.get_all_outputs():
        output_name = output_ch['name']
        active_high = not bool(output_ch['active_low'])
        g_output_channels[output_name] = gpiozero.LED(pin=output_ch['gpio'], active_high=active_high)

    # wrap with error-handling code the main loop
    reconnection_interval_sec = 3
    keyb_interrupted = False
    print(f"Starting main loop")
    while not keyb_interrupted:
        try:
            # Use a task group to manage and await all (endless) tasks
            async with asyncio.TaskGroup() as tg:
                tg.create_task(print_stats_periodically(cfg))
                # inputs:
                tg.create_task(publish_optoisolated_inputs(cfg))
                tg.create_task(process_gpio_inputs_queue_and_publish(cfg))
                # outputs:
                tg.create_task(subscribe_and_activate_outputs(cfg))
                tg.create_task(publish_outputs_state(cfg))

        except* aiomqtt.MqttError as err:
            print(f"Connection lost: {err.exceptions}; reconnecting in {reconnection_interval_sec} seconds ...")
            g_stats['num_connections_lost'] += 1
            await asyncio.sleep(reconnection_interval_sec)
        except* KeyboardInterrupt:
            print_stats()
            print("Stopped by CTRL+C... aborting")
            keyb_interrupted = True
    
    print_stats()
    return 0

# =======================================================================================================
# MAIN
# =======================================================================================================

if __name__ == "__main__":
    if instance_already_running("ha-alarm-raspy2mqtt"):
        print(f"Sorry, detected another instance of this daemon is already running. Using the same I2C bus from 2 sofware programs is not recommended. Aborting.")
        sys.exit(3)

    # configure the PIN factory to be used by gpiozero:
    os.environ["GPIOZERO_PIN_FACTORY"] = "rpigpio"

    try:
        sys.exit(asyncio.run(main_loop()))
    except KeyboardInterrupt:
        print(f"Stopping due to CTRL+C")
