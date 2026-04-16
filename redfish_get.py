import requests

import urllib3
import json
from datetime import datetime
from urllib.parse import urljoin



urllib3.disable_warnings()  # Disable SSL warnings


IP = input("Enter the IP address of the Redfish service: ")
HOST = f"https://{IP}"
USER = input("Enter the username: ")
PASSWORD = input("Enter the password: ")

visited = set()  # To keep track of visited URLs
dump = {}


def walk_and_save(path):
    if path in visited:
        return
    visited.add(path)

    url = urljoin(HOST, path)
    try:
        response = requests.get(url, auth=(USER, PASSWORD), verify=False)
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        print(f"Error fetching {url}: {e}")
        return  
    
    print(f"Visited: {url}")
    dump[path] = data

    for v in data.values():
        if isinstance(v, dict) and '@odata.id' in v:
            walk_and_save(v['@odata.id'])
        elif isinstance(v, list):
            for item in v:
                if isinstance(item, dict) and '@odata.id' in item:
                    walk_and_save(item['@odata.id'])

if __name__ == "__main__":
    walk_and_save('/redfish/v1/')
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    with open(f'redfish_dump_{timestamp}.json', 'w') as f:
        json.dump(dump, f, indent=2, ensure_ascii=False)
    print(f"Data dumped to redfish_dump_{timestamp}.json")