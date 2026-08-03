# Pin collections to the venv's bundled tree (see ansible.cfg for why).
# Computed rather than hardcoded so a rebuilt venv on another Python still works.
export ANSIBLE_COLLECTIONS_PATH := $(shell ./venv/bin/python -c "import ansible_collections,os;print(os.path.dirname(ansible_collections.__path__[0]))")

setup_server:
	ansible-playbook -i inventories/hosts.ini --extra-vars "@variables.yml"  deploy/setup.yml 

flush_iptables:
	ansible-playbook -i inventories/hosts.ini --extra-vars "@variables.yml"  deploy/setup.yml --tags disable-iptables 

deploy_server:
	ansible-playbook -i inventories/hosts.ini --extra-vars "@variables.yml"  deploy/server.yml   

prepare_ssh:
	ansible-playbook -i inventories/hosts.ini --extra-vars "@variables.yml"  deploy/client.yml --tags prepare_ssh   

deploy_client:
	ansible-playbook -i inventories/hosts.ini --extra-vars "@variables.yml"  deploy/client.yml   
