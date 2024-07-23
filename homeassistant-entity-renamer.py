#!/usr/bin/env python3

import argparse
import config
import csv
import json
import re
import requests
import tabulate
import asyncio
import websockets

tabulate.PRESERVE_WHITESPACE = True

# Determine the protocol based on TLS configuration
TLS_S = "s" if config.TLS else ""

# Header containing the access token
headers = {
    "Authorization": f"Bearer {config.ACCESS_TOKEN}",
    "Content-Type": "application/json",
}


def align_strings(table):
    alignment_char = "."

    if len(table) == 0:
        return table

    for column in range(len(table[0])):
        # Get the column data from the table
        column_data = [row[column] for row in table]

        # Find the maximum length of the first part of the split strings
        strings_to_align = [s for s in column_data if alignment_char in s]
        if len(strings_to_align) == 0:
            continue

        max_length = max([len(s.split(alignment_char)[0]) for s in strings_to_align])

        def align_string(s):
            s_split = s.split(alignment_char, maxsplit=1)
            if len(s_split) == 1:
                return s
            else:
                return f"{s_split[0]:>{max_length}}.{s_split[1]}"

        # Create the modified table by replacing the column with aligned strings
        table = [
            tuple(
                align_string(value) if i == column else value
                for i, value in enumerate(row)
            )
            for row in table
        ]

    return table


def list_entities(regex=None):
    # API endpoint for retrieving all entities
    api_endpoint = f"http{TLS_S}://{config.HOST}/api/states"

    # Send GET request to the API endpoint
    response = requests.get(api_endpoint, headers=headers)

    # Check if the request was successful
    if response.status_code == 200:
        # Parse the JSON response
        data = json.loads(response.text)

        # Extract entity IDs and friendly names
        entity_data = [
            (entity["attributes"].get("friendly_name", ""), entity["entity_id"])
            for entity in data
        ]

        # Filter the entity data if regex argument is provided
        if regex:
            filtered_entity_data = [
                (friendly_name, entity_id)
                for friendly_name, entity_id in entity_data
                if re.search(regex, entity_id)
            ]
            entity_data = filtered_entity_data

        # Sort the entity data by friendly name
        entity_data = sorted(entity_data, key=lambda x: x[0])

        # Output the entity data
        return entity_data

    else:
        print(f"Error: {response.status_code} - {response.text}")
        return []


def process_entities(
    entity_data, search_regex, replace_regex=None, output_file=None, input_filename=None
):
    rename_data = []
    if input_filename:
        # Read data from the input file
        with open(input_filename, mode="r") as file:
            reader = csv.DictReader(file)
            for row in reader:
                entity_id = row["Current Entity ID"]
                friendly_name = row.get(
                    "Friendly Name", ""
                )  # Check if 'Friendly Name' is present
                new_entity_id = row.get(
                    "New Entity ID", ""
                )  # Check if 'New Entity ID' is present
                rename_data.append((friendly_name, entity_id, new_entity_id))

        if not rename_data:
            print("No data found in the input file.")
            return

    else:
        if replace_regex:
            for friendly_name, entity_id in entity_data:
                new_entity_id = re.sub(search_regex, replace_regex, entity_id)
                rename_data.append((friendly_name, entity_id, new_entity_id))
        else:
            rename_data = [
                (friendly_name, entity_id, "")
                for friendly_name, entity_id in entity_data
            ]

    # Print the table with friendly name and entity ID
    table = [("Friendly Name", "Current Entity ID", "New Entity ID")] + align_strings(
        rename_data
    )
    print(tabulate.tabulate(table, headers="firstrow", tablefmt="github"))

    # Write to CSV file if output file is provided
    table = [("Friendly Name", "Current Entity ID", "New Entity ID")] + rename_data
    if output_file:
        write_to_csv(table, output_file)

    # Ask user for confirmation if replace_regex is provided or if reading from input file
    if not replace_regex and not input_filename:
        return

    answer = input("\nDo you want to proceed with renaming the entities? (y/N): ")
    if answer.lower() not in ["y", "yes"]:
        print("Renaming process aborted.")
        return

    asyncio.run(rename_entities(rename_data))


async def rename_entities(rename_data):

    ha_url = f"ws{TLS_S}://{config.HOST}/api/websocket"
    auth_msg = json.dumps({"type": "auth", "access_token": config.ACCESS_TOKEN})

    async with websockets.connect(ha_url) as websocket:

        auth_request = await websocket.recv()
        print(f"<<< {auth_request}")

        await websocket.send(auth_msg)
        print(f">>> {auth_msg}")

        auth_result = await websocket.recv()
        print(f"<<< {auth_result}")

        # Rename the entities
        for index, (friendly_name, entity_id, new_entity_id) in enumerate(
            rename_data, start=1
        ):
            entity_registry_update_msg = {
                "id": index,
                "type": "config/entity_registry/update",
                "entity_id": entity_id,
            }
            if new_entity_id:
                entity_registry_update_msg["new_entity_id"] = new_entity_id
            if friendly_name:
                entity_registry_update_msg["name"] = friendly_name

            await websocket.send(json.dumps(entity_registry_update_msg))
            print(f">>> {entity_registry_update_msg}")

            ws_update_result = await websocket.recv()
            print(f"<<< {ws_update_result}")

            update_result = json.loads(ws_update_result)
            if update_result.get("success"):
                success_msg = f"Entity '{entity_id}'"
                if new_entity_id:
                    success_msg += f" renamed to '{new_entity_id}'"
                if friendly_name:
                    success_msg += f" with friendly name '{friendly_name}'"
                success_msg += " successfully!"
                print(success_msg)
            else:
                print(
                    f"Failed to update entity '{entity_id}': {update_result.get('error', {}).get('message', 'Unknown error')}"
                )


def write_to_csv(table, filename):
    with open(filename, mode="w", newline="") as file:
        writer = csv.writer(file)
        writer.writerows(table)
        print(f"(Table written to {filename})")


def main():
    parser = argparse.ArgumentParser(description="HomeAssistant Entity Renamer")
    parser.add_argument(
        "--input-file",
        dest="input_file",
        help="Input CSV file containing Friendly Name, Current Entity ID, and New Entity ID",
    )
    parser.add_argument(
        "--search",
        dest="search_regex",
        help="Regular expression for search. Note: Only searches entity IDs.",
    )
    parser.add_argument(
        "--replace", dest="replace_regex", help="Regular expression for replace"
    )
    parser.add_argument(
        "--output-file",
        dest="output_file",
        help="Output CSV file to export the results",
    )
    args = parser.parse_args()

    # Validate argument combinations
    if args.search_regex and args.input_file:
        print("Error: --search and --input-file cannot be used together.")
        return
    elif args.replace_regex and not args.search_regex:
        print("Error: --replace requires --search.")
        return
    if args.search_regex:
        if entity_data := list_entities(args.search_regex):
            process_entities(
                entity_data, args.search_regex, args.replace_regex, args.output_file
            )
        else:
            print("No entities found matching the search regex.")
    elif args.input_file:
        input_file = args.input_file
        output_file = args.output_file

        process_entities([], None, None, output_file, input_file)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
