#!/usr/bin/env python3

import lib16inpind
import time
import asyncio
import gpiozero
import json
import sys
import aiomqtt
from raspy2mqtt.constants import MqttQOS, SeqMicroHatConstants
from raspy2mqtt.config import AppConfig


#
# Author: fmontorsi
# Created: June 2024
# License: Apache license
#

# =======================================================================================================
# HomeAssistantStatusTracker
# =======================================================================================================

class HomeAssistantStatusTracker:
    """
    This class tracks the HomeAssistant MQTT birth message, which gets published, by default, on the
    topic 'homeassistant/status'.
    When the HomeAssistant status changes to 'online', MQTT discovery messages can be sent.
    """

    # the stop-request is not related to a particular instance of this class... it applies to any instance
    stop_requested = False

    # the MQTT client identifier
    client_identifier = "_homeassistant_status_tracker"

    def __init__(self):
        self.coroutines_list = []
        self.stats = {
            "num_connections_subscribe": 0,
            "num_mqtt_status_msg_processed": 0,
            "ERROR_num_connections_lost": 0,
        }

    def set_discovery_publish_coroutines(self, coroutines_list):
        self.coroutines_list = coroutines_list

    async def subscribe_status(self, cfg: AppConfig):
        """
        Subscribes to HomeAssistant MQTT topic
        """
        print(
            f"Connecting to MQTT broker with identifier {HomeAssistantStatusTracker.client_identifier} to subscribe to HOME ASSISTANT status topic"
        )
        self.stats["num_connections_subscribe"] += 1
        while True:
            try:
                async with cfg.create_aiomqtt_client(HomeAssistantStatusTracker.client_identifier) as client:
                    topic = f"{cfg.homeassistant_discovery_topic_prefix}/status"
                    print(f"HomeAssistantStatusTracker: Subscribing to topic [{topic}]")
                    await client.subscribe(topic)

                    async for message in client.messages:
                        # IMPORTANT: the message.payload and message.topic are not strings and would fail
                        #            a direct comparison to strings... so convert them explicitly to strings first:
                        mqtt_topic = str(message.topic)
                        mqtt_payload = message.payload.decode("UTF-8")

                        self.stats["num_mqtt_status_msg_processed"] += 1
                        if mqtt_payload == "online":
                            print(
                                f"HomeAssistant status changed to 'online'. Sending MQTT discovery messages."
                            )
                            # TODO

                        elif mqtt_payload == "offline":
                            print(
                                f"HomeAssistant status changed to 'offline'."
                            )

            except aiomqtt.MqttError as err:
                print(f"Connection lost: {err}; reconnecting in {cfg.mqtt_reconnection_period_sec} seconds ...")
                self.stats["ERROR_num_connections_lost"] += 1
                await asyncio.sleep(cfg.mqtt_reconnection_period_sec)
            except Exception as err:
                print(f"EXCEPTION: {err}")
                sys.exit(99)


    def print_stats(self):
        print(">> HOME ASSISTANT STATUS TRACKER:")
        print(f">>   Num (re)connections to the MQTT broker [subscribe channel]: {self.stats['num_connections_subscribe']}")
        print(f">>   Num MQTT status messages processed: {self.stats['num_mqtt_status_msg_processed']}")
        print(f">>   ERROR: MQTT connections lost: {self.stats['ERROR_num_connections_lost']}")
