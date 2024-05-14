#!/usr/bin/env python3

import gpiozero, signal, asyncio, queue
from raspy2mqtt.constants import *
from raspy2mqtt.config import *

#
# Author: fmontorsi
# Created: May 2024
# License: Apache license
#

# =======================================================================================================
# GpioInputsHandler
# =======================================================================================================


class GpioInputsHandler:
    """
    This class handles sampling GPIO pins configured for inputs and publishing the results to MQTT.
    It exposes a coroutine that can be 'await'ed, which handles publishing.
    """

    # the stop-request is not related to a particular instance of this class... it applies to any instance
    stop_requested = False

    def __init__(self):
        # thread-safe queue to communicate from GPIOzero secondary threads to main thread
        self.gpio_queue = queue.Queue()

        # in case integration tests are running:
        self.last_emulated_gpio_number = 0

        self.stats = {
            "num_connections_publish": 0,
            "num_gpio_notifications": 0,
            "num_mqtt_messages": 0,
            "ERROR_noconfig": 0,
        }

    def on_gpio_input(self, device):
        """
        This is a gpiozero callback function.
        Remember: gpiozero will invoke such functions from a SECONDARY thread. That's why we use a
        thread-safe queue to communicate back to the main thread (which runs the event loop)
        """
        print(f"!! Detected activation of GPIO{device.pin.number} !! ")
        self.gpio_queue.put(device.pin.number)

    async def emulate_gpio_input(self, sig: signal.Signals) -> None:
        """
        Used for integration tests.
        Emulates a GPIO input activation
        """
        self.last_emulated_gpio_number += 1
        print(f"Received signal {sig.name}: emulating press of GPIO {self.last_emulated_gpio_number}")
        self.gpio_queue.put(self.last_emulated_gpio_number)

    def init_hardware(self, cfg: AppConfig, loop: asyncio.BaseEventLoop) -> list[gpiozero.Button]:
        buttons = []

        if cfg.disable_hw:
            print("Skipping GPIO inputs HW initialization (--disable-hw was given)")

            for sig in [signal.SIGUSR1, signal.SIGUSR2]:
                loop.add_signal_handler(sig, lambda: asyncio.create_task(self.emulate_gpio_input(sig)))

        else:

            # setup GPIO pins for the INPUTs
            print(f"Initializing GPIO input pins")
            for input_ch in cfg.get_all_gpio_inputs():
                # the short hold-time is to ensure that the digital input is served ASAP (i.e. on_gpio_input gets
                # invoked almost immediately)
                active_high = not bool(input_ch["active_low"])
                b = gpiozero.Button(input_ch["gpio"], hold_time=0.1, pull_up=None, active_state=active_high)
                b.when_held = self.on_gpio_input
                buttons.append(b)

        return buttons

    async def process_gpio_inputs_queue_and_publish(self, cfg: AppConfig):
        """
        Publishes over MQTT a message each time a GPIO input changes status.

        This function may throw a aiomqtt.MqttError exception indicating a connection issue!
        """
        print(
            f"Connecting to MQTT broker at address {cfg.mqtt_broker_host}:{cfg.mqtt_broker_port} to publish GPIO INPUT states"
        )
        self.stats["num_connections_publish"] += 1
        async with cfg.create_aiomqtt_client("_gpio_publisher") as client:
            while not GpioInputsHandler.stop_requested:
                # get next notification coming from the gpiozero secondary thread:
                try:
                    gpio_number = self.gpio_queue.get_nowait()
                except queue.Empty:
                    # if there's no notification (typical case), then do not block the event loop
                    # and keep processing other tasks... to ensure low-latency in processing the
                    # GPIO inputs the sleep time is set equal to the MQTT publish freq
                    await asyncio.sleep(cfg.mqtt_publish_period_sec)
                    continue

                # there is a GPIO notification to process:
                gpio_config = cfg.get_gpio_input_config(gpio_number)
                self.stats["num_gpio_notifications"] += 1
                if gpio_config is None or "mqtt" not in gpio_config:
                    print(
                        f"Main thread got notification of GPIO#{gpio_number} being activated but there is NO CONFIGURATION for that pin. Ignoring."
                    )
                    self.stats["ERROR_noconfig"] += 1
                else:
                    # extract MQTT config
                    mqtt_topic = gpio_config["mqtt"]["topic"]
                    mqtt_payload = gpio_config["mqtt"]["payload"]
                    print(
                        f"Main thread got notification of GPIO#{gpio_number} being activated; a valid MQTT configuration is attached: topic={mqtt_topic}, payload={mqtt_payload}"
                    )

                    await client.publish(mqtt_topic, mqtt_payload, qos=MQTT_QOS_AT_LEAST_ONCE)
                    print(f"Sent on topic={mqtt_topic}, payload={mqtt_payload}")
                    self.stats["num_mqtt_messages"] += 1

                self.gpio_queue.task_done()

    def print_stats(self):
        print(f">> GPIO INPUTS:")
        print(f">>   Num (re)connections to the MQTT broker [publish channel]: {self.stats['num_connections_publish']}")
        print(f">>   Num GPIO activations detected: {self.stats['num_gpio_notifications']}")
        print(f">>   Num MQTT messages published to the broker: {self.stats['num_mqtt_messages']}")
        print(f">>   ERROR: GPIO inputs detected but missing configuration: {self.stats['ERROR_noconfig']}")