import pytest
import time
import json

# from testcontainers.core.utils import raise_for_deprecated_parameter

from tests.mosquitto_container import MosquittoContainer
from tests.raspy2mqtt_container import Raspy2MQTTContainer

EXPECTED_DISCOVERY_MSG_OUTPUT_1 = """
{
  "unique_id": "output_1",
  "name": "output_1",
  "command_topic": "rpi2home-assistant/output_1",
  "state_topic": "rpi2home-assistant/output_1/state",
  "device_class": "switch",
  "device": {
    "manufacturer": "github.com/f18m",
    "model": "rpi2home-assistant",
    "name": "integration-test-instance",
    "sw_version": "__THIS_FIELD_IS_NOT_CHECKED__",
    "identifiers": [
      "rpi2home-assistant-integration-test-instance"
    ]
  },
  "payload_on": "ON",
  "payload_off": "OFF"
}
"""

EXPECTED_DISCOVERY_MSG_OPTO_ISOLATED_INPUT_1 = """
{
  "unique_id": "opto_input_1",
  "name": "opto_input_1",
  "state_topic": "rpi2home-assistant/opto_input_1",
  "payload_on": "ON",
  "payload_off": "OFF",
  "device_class": "door",
  "expire_after": 30,
  "device": {
    "manufacturer": "github.com/f18m",
    "model": "rpi2home-assistant",
    "name": "integration-test-instance",
    "sw_version": "__THIS_FIELD_IS_NOT_CHECKED__",
    "identifiers": [
      "rpi2home-assistant-integration-test-instance"
    ]
  }
}
"""


@pytest.mark.integration
def test_mqtt_discovery_messages():

    broker = MosquittoContainer()
    broker.start()

    topics_under_test = [
        {
            "topic_name": "homeassistant/switch/integration-test-instance/output_1/config",
            "expected_msg": EXPECTED_DISCOVERY_MSG_OUTPUT_1,
        },
        {
            "topic_name": "homeassistant/binary_sensor/integration-test-instance/opto_input_1/config",
            "expected_msg": EXPECTED_DISCOVERY_MSG_OPTO_ISOLATED_INPUT_1,
        },
    ]
    broker.watch_topics([x["topic_name"] for x in topics_under_test])

    with Raspy2MQTTContainer(broker) as container:
        time.sleep(1)  # give time to the Raspy2MQTTContainer to fully start
        if not container.is_running():
            print("Container under test has stopped running while broker was still running?? test failed.")
            container.print_logs()
            assert False

        for attempt in range(1, 5):
            if not container.is_running():
                print("Container under test has stopped running unexpectedly!! test failed.")
                container.print_logs()
                assert False

            # now verify discovery messages were produced
            if attempt == 1:
                print("Checking discovery message produced at rpi2home-assistant STARTUP")
            else:
                print(f"Checking discovery message produced after an HomeAssistant RESTART")
            for topic_and_expected in topics_under_test:
                t = topic_and_expected["topic_name"]
                exp = topic_and_expected["expected_msg"]
                expected_dict = json.loads(exp)

                assert broker.get_messages_received_in_watched_topic(t) == attempt

                config_msg = broker.get_last_payload_received_in_watched_topic(t)
                try:
                    config_dict = json.loads(config_msg)
                except json.JSONDecodeError:
                    print(f"The discovery message payload should be a valid JSON. Got instead: [{config_msg}]")
                    container.print_logs()
                    assert False

                # do not compare version numbers inside discovery messages... this is to avoid
                # updating this testcase on every new release:
                del config_dict["device"]["sw_version"]
                del expected_dict["device"]["sw_version"]

                # check also the contents of the discovery message:
                assert config_dict == expected_dict

                print(f"The discovery message on topic [{t}] matches the expected content. Proceeding.")

            # simulate HA start:
            time.sleep(1)
            print("Simulating HomeAssistant start")
            broker.publish_message("homeassistant/status", "online")
            time.sleep(1)

        print("Simulating HomeAssistant stop")
        broker.publish_message("homeassistant/status", "offline")
        time.sleep(1)
        if not container.is_running():
            print("Container under test has stopped running unexpectedly!! test failed.")
            container.print_logs()
            assert False

        broker.unwatch_all()
        print("Integration test passed!")
        # print(f"Sleeping to allow debugging")
        # container.print_logs()
        # time.sleep(50000)

    broker.stop()
