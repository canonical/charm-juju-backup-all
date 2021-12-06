PYTHON := /usr/bin/python3

PROJECTPATH=$(dir $(realpath $(MAKEFILE_LIST)))
ifndef CHARM_BUILD_DIR
	CHARM_BUILD_DIR=${PROJECTPATH}.build
endif
METADATA_FILE="metadata.yaml"
CHARM_NAME=$(shell cat ${PROJECTPATH}/${METADATA_FILE} | grep -E '^name:' | awk '{print $$2}')

help:
	@echo "This project supports the following targets"
	@echo ""
	@echo " make help - show this text"
	@echo " make clean - remove unneeded files"
	@echo " make dev-environment - setup the development environment"
	@echo " make submodules - initialize, fetch, and checkout any nested submodules"
	@echo " make submodules-update - update submodules to latest changes on remote branch"
	@echo " make build - build the charm"
	@echo " make lint - run flake8, black --check and isort --check-only"
	@echo " make reformat - run black and isort and reformat files"
	@echo " make proof - run charm proof"
	@echo " make unittests - run the tests defined in the unittest subdirectory"
	@echo " make functional - run the tests defined in the functional subdirectory"
	@echo " make test - run lint, proof, unittests and functional targets"
	@echo " make pre-commit - run pre-commit checks on all the files"
	@echo ""

clean:
	@echo "Cleaning files"
	@git clean -ffXd -e '!.idea' -e '!.venv'
	@echo "Cleaning existing build"
	@rm -rf ${CHARM_BUILD_DIR}/${CHARM_NAME}

dev-environment:
	@echo "Creating virtualenv and installing pre-commit"
	@virtualenv -p python3 .venv
	@.venv/bin/pip install -r requirements-dev.txt
	@.venv/bin/pre-commit install

submodules:
	@echo "Cloning submodules"
	@git submodule update --init --recursive

submodules-update:
	@echo "Pulling latest updates for submodules"
	@git submodule update --init --recursive --remote --merge

build: clean
	@echo "Building charm to base directory ${CHARM_BUILD_DIR}/${CHARM_NAME}"
	@mkdir -p ${CHARM_BUILD_DIR}/${CHARM_NAME}
	@charmcraft build
	@mv *.charm ${CHARM_BUILD_DIR}/${CHARM_NAME}

lint:
	@echo "Running lint checks"
	@tox -e lint

reformat:
	@echo "Reformat files with black and isort"
	@tox -e reformat

proof:
	@echo "Running charm proof"
	@-charm proof

unittests:
	@echo "Running unit tests"
	@tox -e unit

functional: build
	@echo "Executing functional tests using built charm at ${CHARM_BUILD_DIR}"
	@CHARM_BUILD_DIR=${CHARM_BUILD_DIR} tox -e func

test: lint proof unittests functional
	@echo "Tests completed for charm ${CHARM_NAME}."

pre-commit:
	@tox -e pre-commit

# The targets below don't depend on a file
.PHONY: help submodules submodules-update clean dev-environment build lint reformat proof unittests functional test pre-commit
