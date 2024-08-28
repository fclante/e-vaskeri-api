from flask import Flask, jsonify, request
import requests
import time
import re
import os

app = Flask(__name__)

# Base URLs
BASE_URL = "https://back-nortec.datpro2.dk"

# Login credentials (replace SECRET with actual password)
USERNAME = os.environ.get('E_VASKERI_USERNAME')
PASSWORD = os.environ.get('E_VASKERI_PASSWORD')

# Mapping of machine types and numbers to SVG URLs
MACHINE_URLS = {
    "Vaskemaskine 1": "https://back-nortec.datpro2.dk/download/unit5/A1jHioq8PRKXF1JFkNlRPT5ahDL7es1Z6GZGIkstCrxoGKsqDYZd4KfeIhfGGkxoshylzt84vvCAUyHB9mNA96OCOKQGqBwPAA/4.svg",
    "Vaskemaskine 2": "https://back-nortec.datpro2.dk/download/unit5/A5jHioq8PRKXF1JFkNlRPT5ahDL7es1Z6GZGIkstCrxoGKsqDYZd4KfeIhfGGkxoshylzt84vvCAUyHB9mNAN6OCOKQGqBwPAA/4.svg",
    "Vaskemaskine 3": "https://back-nortec.datpro2.dk/download/unit5/A9jHioq8PRKXF1JFkNlRPT5ahDL7es1Z6GZGIkstCrxoGKsqDYZd4KfeIhfGGkxoshylzt84vvCAUyHB9mNAd6OCOKQGqBwPAA/4.svg",
    "Vaskemaskine 4": "https://back-nortec.datpro2.dk/download/unit5/AxnHioq8PRKXF1JFkNlRPT5ahDL7es1Z6GZGIkstCrxoGKsqDYZd4KfeIhfGGkxoshylzt84vvCAUyHB9mNBt6OCOKQGqBwPAA/4.svg",
    "Vaskemaskine 5": "https://back-nortec.datpro2.dk/download/unit5/A1nHioq8PRKXF1JFkNlRPT5ahDL7es1Z6GZGIkstCrxoGKsqDYZd4KfeIhfGGkxoshylzt84vvCAUyHB9mNB96OCOKQGqBwPAA/4.svg",
    "Vaskemaskine 6": "https://back-nortec.datpro2.dk/download/unit5/A5nHioq8PRKXF1JFkNlRPT5ahDL7es1Z6GZGIkstCrxoGKsqDYZd4KfeIhfGGkxoshylzt84vvCAUyHB9mNBN6OCOKQGqBwPAA/4.svg",
    "Vaskemaskine 7": "https://back-nortec.datpro2.dk/download/unit5/A9nHioq8PRKXF1JFkNlRPT5ahDL7es1Z6GZGIkstCrxoGKsqDYZd4KfeIhfGGkxoshylzt84vvCAUyHB9mNBd6OCOKQGqBwPAA/4.svg"
}

# Mapping of SVG element IDs to friendly names
KEY_MAPPING = {
    r'P808LAU\d': 'program',
    r'C808LAU\d': 'currentStep',
    r'S808LAU\d': 'status',
    r'R808LAU\d': 'remainingTime'
}

def get_epoch():
    """Get the current epoch time in milliseconds."""
    return int(time.time() * 1000)

@app.route('/login', methods=['GET'])
def login():
    """Login to the system and get a session token."""
    tick = get_epoch()
    login_url = f"{BASE_URL}/User/Login4/?App=TUK&session=&username={USERNAME}&password={PASSWORD}&language=da&guid=&tick={tick}&domain=https%3A%2F%2Fe-vaskeri.dk&native=false&.json"
    
    response = requests.get(login_url)
    if response.status_code == 200:
        session_token = response.json().get('Session')
        return jsonify({"session": session_token})
    else:
        return jsonify({"error": "Failed to log in"}), response.status_code

@app.route('/running-machines', methods=['GET'])
def running_machines():
    """Get the status of running machines."""
    session_token = request.args.get('session')
    if not session_token:
        return jsonify({"error": "Session token is required"}), 400

    tick = get_epoch()
    url = f"{BASE_URL}/Location/Live5/?App=TUK&session={session_token}&tick={tick}&native=false&.json"
    
    response = requests.get(url)
    print(f'response: {response.content}')
    if response.status_code == 200:
        return jsonify(response.json())
    else:
        return jsonify({"error": "Failed to retrieve running machines"}), response.status_code

@app.route('/balance', methods=['GET'])
def balance():
    """Get the user's balance."""
    session_token = request.args.get('session')
    if not session_token:
        return jsonify({"error": "Session token is required"}), 400

    tick = get_epoch()
    url = f"{BASE_URL}/User/Statement5/?App=TUK&session={session_token}&tick={tick}&native=false&.json"
    
    
    response = requests.get(url)
    response.content
    print(f'response: {response.content}')

    if response.status_code == 200:
        return jsonify(response.json())
    else:
        return jsonify({"error": "Failed to retrieve balance"}), response.status_code

@app.route('/status', methods=['GET'])
def status():
    """Get the status of a specific washing machine."""
    machine_type = request.args.get('machineType')
    machine_number = request.args.get('machineNumber')
    
    machine_key = f"{machine_type} {machine_number}"
    svg_url = MACHINE_URLS.get(machine_key)
    
    if not svg_url:
        return jsonify({"error": "Invalid machine type or number"}), 400
    
    response = requests.get(svg_url)
    if response.status_code != 200:
        return jsonify({"error": "Failed to download SVG"}), response.status_code
    
    svg_content = response.text
    extracted_data = {}
    
    for regex_pattern, friendly_name in KEY_MAPPING.items():
        match = re.search(regex_pattern, svg_content)
        if match:
            matched_value = re.findall(rf'<text id="{match.group(0)}".*?>(.*?)</text>', svg_content, re.DOTALL)
            extracted_data[friendly_name] = matched_value[0].strip() if matched_value else None

    return jsonify(extracted_data)

if __name__ == '__main__':
    app.run(port=5000)
