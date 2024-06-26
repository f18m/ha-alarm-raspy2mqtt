import pytest
import platform
from raspy2mqtt.config import AppConfig


@pytest.mark.unit
def test_nonexisting_config_file_fails():
    x = AppConfig()
    assert x.load("a/path/that/does/not/exists") == False


@pytest.mark.unit
def test_empty_config_file_fails(tmpdir):
    # create config file to test:
    p = tmpdir.mkdir("cfg").join("testconfig.yaml")
    p.write("\n")

    x = AppConfig()
    assert x.load(str(p)) == False


MINIMAL_CFG = """
mqtt_broker:
  host: something
"""


@pytest.mark.unit
def test_minimal_config_file_succeeds(tmpdir):
    # create config file to test:
    p = tmpdir.mkdir("cfg").join("testconfig.yaml")
    p.write(MINIMAL_CFG)

    x = AppConfig()
    assert x.load(str(p)) == True  # it should be loadable

    assert x.mqtt_broker_host == "something"
    assert x.mqtt_broker_user is None
    assert x.get_optoisolated_input_config(1) is None
    assert len(x.get_all_gpio_inputs()) == 0
    assert len(x.get_all_outputs()) == 0


CFG_USING_DEFAULTS = """
mqtt_broker:
  host: something
i2c_optoisolated_inputs:
  - name: opto_input_1
    # no description, it's optional
    input_num: 1
    active_low: true
    # no mqtt, it's optional
    home_assistant:
      device_class: tamper
gpio_inputs:
  - name: radio_channel_a
    # no description, it's optional
    gpio: 27
    active_low: false
    mqtt:
      topic: alarmo/command
      payload: ARM_AWAY
outputs:
  - name: ext_alarm_siren
    # no description, it's optional
    gpio: 20
    active_low: true
    # no mqtt, it's optional
    home_assistant:
      device_class: switch
"""


@pytest.mark.unit
def test_config_file_using_defaults_succeeds(tmpdir):
    # create config file to test:
    p = tmpdir.mkdir("cfg").join("testconfig.yaml")
    p.write(CFG_USING_DEFAULTS)

    x = AppConfig()
    assert x.load(str(p)) == True

    # MQTT BROKER
    assert x.mqtt_broker_host == "something"
    assert x.mqtt_reconnection_period_sec == 1
    assert x.mqtt_broker_user is None
    assert x.mqtt_broker_password is None

    # HOME ASSISTANT section
    assert x.homeassistant_default_topic_prefix == "rpi2home-assistant"
    assert x.homeassistant_publish_period_sec == 1
    assert x.homeassistant_discovery_messages_enable == True
    assert x.homeassistant_discovery_topic_prefix == "homeassistant"
    assert x.homeassistant_discovery_topic_node_id == platform.node()

    # OPTO-ISOLATED INPUTS
    # check that all attributes have been populated with the defaults:
    assert x.get_optoisolated_input_config(1) == {
        "active_low": True,
        "description": "opto_input_1",
        "filter": {"stability_threshold_sec": 0},
        "home_assistant": {"device_class": "tamper", "expire_after": 30, "icon": None, "platform": "binary_sensor"},
        "input_num": 1,
        "mqtt": {"payload_off": "OFF", "payload_on": "ON", "topic": "rpi2home-assistant/opto_input_1"},
        "name": "opto_input_1",
    }

    # GPIO INPUTS
    assert len(x.get_all_gpio_inputs()) == 1
    assert x.get_gpio_input_config(1) == None  # '1' is not a valid GPIO input
    assert x.get_gpio_input_config(27) == {
        "active_low": False,
        "description": "radio_channel_a",
        "gpio": 27,
        "mqtt": {"payload": "ARM_AWAY", "topic": "alarmo/command"},
        "name": "radio_channel_a",
    }

    # GPIO OUTPUTs
    assert len(x.get_all_outputs()) == 1
    assert x.get_output_config_by_mqtt_topic("non-existing") == None
    assert x.get_output_config_by_mqtt_topic("rpi2home-assistant/ext_alarm_siren") == {
        "active_low": True,
        "description": "ext_alarm_siren",
        "home_assistant": {"device_class": "switch", "expire_after": 30, "icon": None, "platform": "switch"},
        "gpio": 20,
        "mqtt": {
            "payload_off": "OFF",
            "payload_on": "ON",
            "topic": "rpi2home-assistant/ext_alarm_siren",
            "state_topic": "rpi2home-assistant/ext_alarm_siren/state",
        },
        "name": "ext_alarm_siren",
    }


CFG_FULLY_SPECIFIED = """
mqtt_broker:
  host: something
  reconnection_period_msec: 1
  user: foo
  password: bar
home_assistant:
  default_topic_prefix: justaprefix
  publish_period_msec: 2
  discovery_messages:
    enable: false
    topic_prefix: anotherprefix
    node_id: some_unique_device_id
i2c_optoisolated_inputs:
  - name: opto_input_1
    description: just a test
    input_num: 1
    active_low: true
    mqtt:
      payload_on: FOO
      payload_off: BAR
      topic: test_topic_1
    home_assistant:
      platform: binary_sensor
      device_class: tamper
      expire_after: 1000
      icon: mdi:check-circle
    filter:
      stability_threshold_sec: 3
gpio_inputs:
  - name: radio_channel_a
    description: yet another test
    gpio: 27
    active_low: false
    mqtt:
      topic: test_topic_2
      payload: JUST_ONE_PAYLOAD_FOR_GPIO_INPUTS
outputs:
  - name: a_button
    description: yet another test
    gpio: 20
    active_low: true
    mqtt:
      payload_on: FOO
      payload_off: BAR
      topic: test_topic_3
      state_topic: test_state_topic_3
    home_assistant:
      platform: button
      device_class: restart
      expire_after: 1000
      icon: mdi:alarm-bell
"""


@pytest.mark.unit
def test_config_file_fully_specified_succeeds(tmpdir):
    # create config file to test:
    p = tmpdir.mkdir("cfg").join("testconfig.yaml")
    p.write(CFG_FULLY_SPECIFIED)

    x = AppConfig()
    assert x.load(str(p)) == True

    # MQTT BROKER section
    assert x.mqtt_broker_host == "something"
    assert x.mqtt_reconnection_period_sec == 0.001
    assert x.mqtt_broker_user == "foo"
    assert x.mqtt_broker_password == "bar"

    # HOME ASSISTANT section
    assert x.homeassistant_default_topic_prefix == "justaprefix"
    assert x.homeassistant_publish_period_sec == 0.002
    assert x.homeassistant_discovery_messages_enable == False
    assert x.homeassistant_discovery_topic_prefix == "anotherprefix"
    assert x.homeassistant_discovery_topic_node_id == "some_unique_device_id"

    # OPTO-ISOLATED INPUTS
    # check that all attributes have been populated with the defaults:
    assert x.get_optoisolated_input_config(1) == {
        "active_low": True,
        "description": "just a test",
        "filter": {
            "stability_threshold_sec": 3,
        },
        "home_assistant": {
            "device_class": "tamper",
            "expire_after": 1000,
            "icon": "mdi:check-circle",
            "platform": "binary_sensor",
        },
        "input_num": 1,
        "mqtt": {"payload_off": "BAR", "payload_on": "FOO", "topic": "test_topic_1"},
        "name": "opto_input_1",
    }

    # GPIO INPUTS
    assert len(x.get_all_gpio_inputs()) == 1
    assert x.get_gpio_input_config(1) == None  # '1' is not a valid GPIO input
    assert x.get_gpio_input_config(27) == {
        "active_low": False,
        "description": "yet another test",
        "gpio": 27,
        "mqtt": {"payload": "JUST_ONE_PAYLOAD_FOR_GPIO_INPUTS", "topic": "test_topic_2"},
        "name": "radio_channel_a",
    }

    # GPIO OUTPUTs
    assert len(x.get_all_outputs()) == 1
    assert x.get_output_config_by_mqtt_topic("non-existing") == None
    assert x.get_output_config_by_mqtt_topic("test_topic_3") == {
        "active_low": True,
        "description": "yet another test",
        "home_assistant": {
            "device_class": "restart",
            "expire_after": 1000,
            "icon": "mdi:alarm-bell",
            "platform": "button",
        },
        "gpio": 20,
        "mqtt": {
            "payload_off": "BAR",
            "payload_on": "FOO",
            "topic": "test_topic_3",
            "state_topic": "test_state_topic_3",
        },
        "name": "a_button",
    }


INVALID_INPUTNUM_CFG = """
mqtt_broker:
  host: something
i2c_optoisolated_inputs:
  - name: test
    input_num: 20  # but the board has only 16 channels
gpio_inputs: []
outputs: []
"""


@pytest.mark.unit
def test_wrong_config_file_fails_1(tmpdir):
    # create config file to test:
    p = tmpdir.mkdir("cfg").join("testconfig.yaml")
    p.write(INVALID_INPUTNUM_CFG)

    x = AppConfig()
    assert x.load(str(p)) == False


INVALID_HA_PLATFORM_CFG = """
mqtt_broker:
  host: something
i2c_optoisolated_inputs:
  - name: test
    input_num: 10
    active_low: false
    home_assistant:
      platform: light    # light is not supported
      device_class: light
gpio_inputs: []
outputs: []
"""


@pytest.mark.unit
def test_wrong_config_file_fails_2(tmpdir):
    # create config file to test:
    p = tmpdir.mkdir("cfg").join("testconfig.yaml")
    p.write(INVALID_HA_PLATFORM_CFG)

    x = AppConfig()
    assert x.load(str(p)) == False


INVALID_HA_DEVICECLASS_CFG = """
mqtt_broker:
  host: something
i2c_optoisolated_inputs:
  - name: test
    input_num: 10
    active_low: false
    home_assistant:
      platform: binary_sensor
      device_class: a-non-existing-devclass
gpio_inputs: []
outputs: []
"""


@pytest.mark.unit
def test_wrong_config_file_fails_3(tmpdir):
    # create config file to test:
    p = tmpdir.mkdir("cfg").join("testconfig.yaml")
    p.write(INVALID_HA_DEVICECLASS_CFG)

    x = AppConfig()
    assert x.load(str(p)) == False
