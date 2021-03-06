import os
import requests
from random import choice
from flask import Flask, render_template, request
from common.network_info import cid_size, get_url, domain_names
from common.encoding import json_to_bytes, bytes_to_json
from crypto.random_bytes import generate_bytes


class Tracker(Flask):
    def __init__(self):
        super().__init__(__name__, template_folder=os.path.abspath('tracker/templates'))
        self.add_url_rule("/", "index", self.index, methods=["GET"])
        self.add_url_rule("/", "main_handler", self.main_handler, methods=["POST"])

        self.files = {}

        self.peers = {}

        self.fsid_counter = 0

    def index(self):
        """Renders template to be displayed on a browser.
        """
        # Process list of files a client has
        return render_template("index.html",
                               data={"file_list": list(self.files.keys()),
                                     "peers": {cid: domain_names[ip] for cid, ip in self.peers.items()}})

    def main_handler(self):
        """The main handler can get two types of requests:

        1) a message containing the list of files a client has

        2) a message containing a request for a certain file.

        """
        message = request.get_json()
        payload = bytes_to_json(bytes.fromhex(message["payload"]))

        # A new client connects to the network by sending the list of
        # files it
        if payload["type"] == "ls":
            return self.handle_new_client(message["CID"], request.remote_addr, payload["files"])
        elif payload["type"] == "teardown":
            self.teardown(message["CID"])
            return "ok"
        # A client sends a file request, this is the only other
        # possibility
        elif payload["type"] == "request":
            return self.handle_file_request(message["CID"], payload["file"])
        else:
            return ("Unexpected payload type: " + payload["type"], 400) # 400 Bad Request

    def handle_new_client(self, cid, ip, files):
        """Registers a new client by remembering the CID and IP of the exit
        node, and send back the list of available files.

        """
        self.peers[cid] = ip

        for file in files:
            # Create a list of owning clients for the file if there is none,
            # and put the cid of the client in it
            self.files.setdefault(file, []).append(cid)

        # Send a new list of files to all peers
        for peer_cid, peer_ip in self.peers.items():
            response = {
                "CID": peer_cid,
                "payload": json_to_bytes({
                    "type": "ls",
                    "files": list(self.files.keys())
                }).hex()
            }
            requests.post(get_url(peer_ip), json=response)

        return "ok"

    def handle_file_request(self, request_client_cid, file):
        """Checks availability of the requested file and creates a bridge if
        available.

        """
        if file not in self.files:
            return ("file not found in network files: " + file, 404) # 404 Not Found

        request_client_ip = self.peers[request_client_cid]

        # Find the CID and IP of the first client owning the requested file
        owning_client_cid = choice(self.files[file])
        owning_client_ip = self.peers[owning_client_cid]

        # Create the CID and FSID for this bridge
        bridge_cid = generate_bytes(cid_size).hex()
        fsid = self.fsid_counter
        self.fsid_counter += 1

        # Send a message to the node at the start of the bridge
        make_bridge_message = {
            "type": "make_bridge",
            "bridge_CID": bridge_cid,
            "to": request_client_ip,
            "FSID": fsid
        }
        # Send on the control channel of the node
        requests.post(get_url(owning_client_ip) + "/control", json=make_bridge_message)

        # Send a message to the node at the end of the bridge
        receive_bridge_message = {
            "type": "receive_bridge",
            "CID": request_client_cid,
            "bridge_CID": bridge_cid
        }
        # Send on the control channel of the node
        requests.post(get_url(request_client_ip) + "/control", json=receive_bridge_message)

        # Send a message to the client, asking he/she to send the file
        request_message = {
            "CID": owning_client_cid,
            "payload": json_to_bytes({
                "type": "request",
                "file": file,
                "FSID": fsid
            }).hex()
        }
        requests.post(get_url(owning_client_ip), json=request_message)
        return "ok"

    def teardown(self, cid):
        del self.peers[cid]
        to_be_removed = []
        for file in self.files.keys():
            if cid in self.files[file]:
                self.files[file].remove(cid)
                if not self.files[file]:
                    to_be_removed.append(file)
        for file in to_be_removed:
            del self.files[file]


tracker = Tracker()
tracker.run(host='0.0.0.0', use_reloader=False)
