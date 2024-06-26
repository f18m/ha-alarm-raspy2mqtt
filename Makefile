#
# This Makefile can be used to install this project on a Raspbian OS.
# The specifics of Raspbian OS are that:
#  * pip3 install <module-name> will not work and fail with msg:
#     "error: externally-managed-environment [...]"
#
# So this makefile takes care of automating
#  * creation of venv
#  * installation of this project into that venv
#  * distribution of config file in standard system folders

SHELL = /bin/bash

ifeq ($(BINDEST),)
BINDEST=/root
endif
ifeq ($(CFGDEST),)
CFGDEST=/etc
endif
ifeq ($(SYSTEMDUNITDEST),)
SYSTEMDUNITDEST=/lib/systemd/system/
endif
ifeq ($(CONFIG_FILE_FOR_DOCKER),)
# NOTE: please override this with a config file containing a valid MQTT broker config
CONFIG_FILE_FOR_DOCKER=$(shell pwd)/tests/integration-test-config.yaml
endif

#
# TARGETS FOR DEPLOYING ON RaspBian
#

raspbian_install:
	# check OS version
	@if [[ `lsb_release -i -s 2>/dev/null` != "Raspbian" && `lsb_release -i -s 2>/dev/null` != "Debian" ]] || (( `lsb_release -r -s 2>/dev/null` < 12 )); then \
		echo ; \
		echo "** WARNING **" ; echo "THIS SOFTWARE HAS BEEN TESTED ONLY ON RASPBIAN 12 OR HIGHER AND REQUIRES PYTHON3.11" ; \
		echo "CHECK IF THIS DISTRIBUTION IS OK... PROCEEDING BUT EXPECT ERRORS" ; \
		echo ; \
	else \
		echo "Your operating system seems to be OK for rpi2home-assistant" ; \
	fi
	# install python venv
	python3 -m venv $(BINDEST)/rpi2home-assistant-venv
	$(BINDEST)/rpi2home-assistant-venv/bin/pip3 install .
	# install app config (only if MISSING, don't overwrite customizations)
	@if [[ -f $(CFGDEST)/rpi2home-assistant.yaml ]]; then \
		echo "WARNING: a configuration file already exists; copying the updated one with .new suffix" ; \
		cp -av config.yaml $(CFGDEST)/rpi2home-assistant.yaml.new ; \
	else \
		cp -av config.yaml $(CFGDEST)/rpi2home-assistant.yaml ; \
	fi
	# install systemd config
	chmod 644 systemd/*.service
	cp -av systemd/*.service $(SYSTEMDUNITDEST)/
	systemctl daemon-reload

raspbian_enable_at_boot:
	systemctl enable rpi2home-assistant.service
	# this is assuming that the Debian package "pigpiod" is already installed:
	systemctl enable pigpiod.service

raspbian_start:
	systemctl start rpi2home-assistant.service
	systemctl start pigpiod.service

raspbian_show_logs:
	journalctl -u rpi2home-assistant

raspbian_update_dependencies:
	$(BINDEST)/rpi2home-assistant-venv/bin/pip3 install --upgrade .

raspbian_uninstall:
	@rm -fv $(SYSTEMDUNITDEST)/rpi2home-assistant.service
	systemctl daemon-reload
	@echo "Considering deleting also:"
	@echo " $(CFGDEST)/rpi2home-assistant.yaml/*"
	@echo " $(BINDEST)/rpi2home-assistant-venv"

#
# TARGETS FOR DEVELOPMENT
#

docker:
	docker build -t rpi2home-assistant:latest .

run-docker:
	@if [ ! -f $(CONFIG_FILE_FOR_DOCKER) ]; then \
		echo "Could not find the config file $(CONFIG_FILE_FOR_DOCKER) to mount inside the docker... please specify a valid config file with CONFIG_FILE_FOR_DOCKER option." ; \
		exit 3 ; \
	fi
	docker run --rm -ti --env DISABLE_HW=1 --network=host \
		-v $(CONFIG_FILE_FOR_DOCKER):/etc/rpi2home-assistant.yaml \
		rpi2home-assistant:latest

run-mosquitto:
	docker run -d --publish 1883:1883 \
		--volume $$(pwd)/tests/integration-test-mosquitto.conf:/mosquitto/config/mosquitto.conf \
		eclipse-mosquitto:latest

test: unit-test integration-test

unit-test:
ifeq ($(REGEX),)
	pytest -vvv --log-level=INFO -m unit
else
	pytest -vvvv --log-level=INFO -s -m unit -k $(REGEX)
endif

integration-test:
ifeq ($(REGEX),)
	pytest -vvvv --log-level=INFO -s -m integration
else
	pytest -vvvv --log-level=INFO -s -m integration -k $(REGEX)
endif

format:
	black .

lint:
	ruff check raspy2mqtt/
