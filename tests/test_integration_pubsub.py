import pytest
import time
import signal

from tests.mosquitto_container import MosquittoContainerEnhanced
from tests.raspy2mqtt_container import Raspy2MQTTContainer


# GLOBALs

broker = MosquittoContainerEnhanced()

# HELPERS


@pytest.fixture(scope="module", autouse=True)
def setup(request):
    """
    Fixture to setup and teardown the MQTT broker
    """
    broker.start()
    print("Broker successfully started")

    def remove_container():
        broker.stop()

    request.addfinalizer(remove_container)


# TESTS


@pytest.mark.integration
def test_publish_for_optoisolated_inputs():

    topics_under_test = ["rpi2home-assistant/opto_input_1", "rpi2home-assistant/opto_input_2"]
    min_expected_msg = 10
    expected_msg_rate = 2  # in msgs/sec; see the 'publish_period_msec' inside Raspy2MQTTContainer.CONFIG_FILE

    with Raspy2MQTTContainer(broker=broker) as container:
        time.sleep(1)  # give time to the Raspy2MQTTContainer to fully start
        if not container.is_running():
            print("Container under test has stopped running... test failed.")
            container.print_logs()
            assert False

        broker.watch_topics(topics_under_test)  # start watching topic only after start to get accurate msg rate
        print(f"Waiting 6 seconds to measure msg rate in topics: {topics_under_test}")
        time.sleep(6)

        broker.print_logs()
        container.print_logs()

        print(f"Total messages received by the broker: {broker.get_messages_received()}")
        for t in topics_under_test:
            msg_count = broker.get_messages_received_in_watched_topic(t)
            msg_rate = broker.get_message_rate_in_watched_topic(t)
            print(f"** TEST RESULTS [{t}]")
            print(f"Total messages in topic [{t}]: {msg_count} msgs")
            print(f"Msg rate in topic [{t}]: {msg_rate} msgs/sec")

            def almost_equal(x, y, threshold=0.5):
                return abs(x - y) < threshold

            # checks
            assert msg_count >= min_expected_msg
            assert almost_equal(msg_rate, expected_msg_rate)

        broker.unwatch_all()
        print("Integration test passed!")


@pytest.mark.integration
def test_publish_for_gpio_inputs():

    topics_under_test = [
        {"topic_name": "gpio1", "expected_payload": "HEY"},
        {"topic_name": "gpio4", "expected_payload": "BYEBYE"},
    ]

    with Raspy2MQTTContainer(broker=broker) as container:
        time.sleep(1)  # give time to the Raspy2MQTTContainer to fully start
        if not container.is_running():
            print("Container under test has stopped running... test failed.")
            container.print_logs()
            assert False

        broker.watch_topics([t["topic_name"] for t in topics_under_test])

        container.get_wrapped_container().kill(signal.SIGUSR1)  # gpio #1 should trigger an MQTT message
        time.sleep(1)
        container.get_wrapped_container().kill(signal.SIGUSR1)  # gpio #2 should be skipped since it's unconfigured
        container.get_wrapped_container().kill(signal.SIGUSR1)  # gpio #3 should be skipped since it's unconfigured
        container.get_wrapped_container().kill(signal.SIGUSR1)  # gpio #4 should trigger an MQTT message
        time.sleep(1)

        container.print_logs()

        for t in topics_under_test:
            tname = t["topic_name"]
            msg_count = broker.get_messages_received_in_watched_topic(tname)
            last_payload = broker.get_last_payload_received_in_watched_topic(tname)
            print(f"** TEST RESULTS [{tname}]")
            print(f"Total messages in topic [{tname}]: {msg_count} msgs")
            print(f"Last payload in topic [{tname}]: {last_payload}")
            assert msg_count == 1
            assert last_payload == t["expected_payload"]

        broker.unwatch_all()
        print("Integration test passed!")


@pytest.mark.integration
def test_publish_subscribe_for_outputs():

    test_runs = [
        {"topic_name": "rpi2home-assistant/output_1", "payload": "ON", "expected_file_contents": "20: ON"},
        {"topic_name": "rpi2home-assistant/output_1", "payload": "OFF", "expected_file_contents": "20: OFF"},
        {"topic_name": "rpi2home-assistant/output_2", "payload": "OFF", "expected_file_contents": "21: OFF"},
        {"topic_name": "rpi2home-assistant/output_2", "payload": "ON", "expected_file_contents": "21: ON"},
    ]
    INTEGRATION_TESTS_OUTPUT_FILE = "/tmp/integration-tests-output"

    def get_associated_state_topic(topic_name: str):
        return topic_name + "/state"

    with Raspy2MQTTContainer(broker=broker) as container:
        time.sleep(1)  # give time to the Raspy2MQTTContainer to fully start
        if not container.is_running():
            print("Container under test has stopped running... test failed.")
            container.print_logs()
            assert False

        i = 0
        for t in test_runs:
            state_topic = get_associated_state_topic(t["topic_name"])
            broker.watch_topics([state_topic])

            # send on the broker a msg
            print(f"TEST#{i}: Asking the software to drive the output [{t['topic_name']}] to state [{t['payload']}]")
            broker.publish_message(t["topic_name"], t["payload"])

            # give time to the app to react to the published message:
            time.sleep(1)

            broker.print_logs()
            container.print_logs()

            # verify file gets written inside /tmp
            with open(INTEGRATION_TESTS_OUTPUT_FILE, "r") as opened_file:
                assert opened_file.read() == t["expected_file_contents"]

            # verify that the STATE TOPIC in the broker has been updated:
            msg_count = broker.get_messages_received_in_watched_topic(state_topic)
            last_payload = broker.get_last_payload_received_in_watched_topic(state_topic)
            assert (
                msg_count >= 1
            )  # the software should publish an initial message and then an update after it processes the request to change output state
            assert last_payload == t["payload"]
            broker.unwatch_all()

            i += 1

        broker.unwatch_all()
        print("Integration test passed!")
