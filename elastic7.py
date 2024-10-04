import boto3
import requests
import time
import sys
import json
import os

FAILED_IPS_FILE = "failed_ips.txt"  # The file to store failed IPs' first three octets
STATE_FILE = "allocation_state.json"  # The file to store state between restarts
INSTANCE_ID = "i-021922607f3fbeb53"  # Hardcoded EC2 instance ID for the association

def allocate_elastic_ips(num_ips_to_allocate):
    ec2_client = boto3.client('ec2')
    allocated_ips = []
    allocation_ids = []
    
    for _ in range(num_ips_to_allocate):
        try:
            response = ec2_client.allocate_address(Domain='vpc')
            ip_address = response['PublicIp']
            allocation_id = response['AllocationId']
            allocated_ips.append(ip_address)
            allocation_ids.append(allocation_id)
        except ec2_client.exceptions.AddressLimitExceeded:
            print("Address limit exceeded. No more IPs can be allocated.")
            sys.exit(1)  # Exit when the limit is exceeded
        except Exception as e:
            print(f"Failed to allocate IP: {e}")
    
    return allocated_ips, allocation_ids

def release_elastic_ip(ip_address, allocation_id):
    ec2_client = boto3.client('ec2')
    try:
        ec2_client.release_address(AllocationId=allocation_id)
        print(f"Released IP: {ip_address}")
    except Exception as e:
        print(f"Failed to release IP {ip_address}: {e}")

def disassociate_elastic_ip(allocation_id):
    ec2_client = boto3.client('ec2')
    try:
        ec2_client.disassociate_address(AllocationId=allocation_id)
        print(f"Disassociated IP with allocation ID: {allocation_id}")
    except Exception as e:
        print(f"Failed to disassociate IP with allocation ID {allocation_id}: {e}")

def associate_elastic_ip(instance_id, elastic_ip):
    ec2_client = boto3.client('ec2')
    try:
        ec2_client.associate_address(InstanceId=instance_id, PublicIp=elastic_ip)
        print(f"Associated Elastic IP {elastic_ip} with instance {instance_id}")
    except Exception as e:
        print(f"Failed to associate Elastic IP: {e}")
        return False
    return True

def check_proxy(elastic_ip):
    proxy_url = f"http://{elastic_ip}:3128"  # No authentication, just IP and port
    proxies = {"http": proxy_url, "https": proxy_url}  # Use the same proxy for both HTTP and HTTPS
    
    try:
        # Attempt to connect through the proxy
        response = requests.get("https://www.irctc.co.in/nget/train-search", proxies=proxies, timeout=10)
        if response.status_code == 200:
            print(f"Proxy is working. Successfully accessed IRCTC through {elastic_ip}")
            return True
        else:
            print(f"Failed to access IRCTC through the proxy. Status Code: {response.status_code}")
            return False
    except requests.exceptions.RequestException as e:
        print(f"Failed to access IRCTC through the proxy. Error: {e}")
        return False

def save_failed_ip_to_file(ip):
    ip_prefix = ".".join(ip.split('.')[:3])  # Get the first three octets
    with open(FAILED_IPS_FILE, "a") as file:
        file.write(ip_prefix + "\n")  # Write the prefix to the file
    print(f"Saved failed IP prefix {ip_prefix} to {FAILED_IPS_FILE}")
    push_failed_ips_to_repo()  # Push the updated list to the repository

def push_failed_ips_to_repo():
    try:
        if not os.path.exists('.git'):
            print("Error: Not a git repository. Please initialize a git repository first.")
            return

        os.system(f"git add {FAILED_IPS_FILE}")
        os.system("git commit -m 'Updated failed IPs'")
        os.system("git push origin main")  # Assuming the default branch is 'main'
    except Exception as e:
        print(f"Error pushing to repository: {e}")

def load_failed_ips():
    ip_list = set()
    try:
        with open(FAILED_IPS_FILE, "r") as file:
            for line in file:
                ip_list.add(line.strip())
    except FileNotFoundError:
        # File doesn't exist, so no failed IPs loaded
        pass
    return ip_list  # Return as a set for faster lookups

def filter_kept_ips(allocated_ips, allocation_ids, failed_ips):
    kept_ips = []
    kept_allocation_ids = []
    released_ips = []

    for ip, allocation_id in zip(allocated_ips, allocation_ids):
        ip_prefix = ".".join(ip.split('.')[:3])

        # If the IP prefix is in the failed IPs list, release it without checking
        if ip_prefix in failed_ips:
            release_elastic_ip(ip, allocation_id)
            released_ips.append(ip)
        else:
            kept_ips.append(ip)
            kept_allocation_ids.append(allocation_id)

    return kept_ips, kept_allocation_ids, len(kept_ips)

def countdown_timer(seconds):
    for remaining in range(seconds, 0, -1):
        sys.stdout.write(f"\rWaiting for {remaining} seconds...")
        sys.stdout.flush()
        time.sleep(1)
    print("\n")

def save_state(num_ips_to_allocate, kept_ips, kept_allocation_ids):
    state = {
        "num_ips_to_allocate": num_ips_to_allocate,
        "kept_ips": kept_ips,
        "kept_allocation_ids": kept_allocation_ids
    }
    with open(STATE_FILE, "w") as file:
        json.dump(state, file)
    print(f"Saved state to {STATE_FILE}")

def load_state():
    try:
        with open(STATE_FILE, "r") as file:
            state = json.load(file)
            return state.get("num_ips_to_allocate"), state.get("kept_ips"), state.get("kept_allocation_ids")
    except FileNotFoundError:
        return None, [], []

def main():
    # Load previously failed IP prefixes
    failed_ips = load_failed_ips()

    # Load previous state if exists
    num_ips_to_allocate, kept_ips, kept_allocation_ids = load_state()

    if num_ips_to_allocate is None:
        try:
            target_kept_ips = int(input("Enter the desired number of kept IPs: "))
            num_ips_to_allocate = target_kept_ips
        except ValueError:
            print("Invalid input. Please enter a number.")
            return
    else:
        target_kept_ips = num_ips_to_allocate + len(kept_ips)
        print(f"Resuming with {num_ips_to_allocate} IPs to allocate and {len(kept_ips)} already kept IPs.")

    while len(kept_ips) < target_kept_ips:
        # Step 1: Allocate Elastic IPs
        allocated_ips, allocation_ids = allocate_elastic_ips(num_ips_to_allocate)
        print(f"Allocated IPs: {allocated_ips}")

        # Step 2: Filter out kept and released IPs
        filtered_ips, filtered_allocation_ids, kept_count = filter_kept_ips(allocated_ips, allocation_ids, failed_ips)

        # Step 3: Only work with filtered IPs
        for ip, allocation_id in zip(filtered_ips, filtered_allocation_ids):
            # Associate filtered IP with the instance
            if associate_elastic_ip(INSTANCE_ID, ip):
                # Wait for association to take effect
                time.sleep(10)  # Waiting 10 seconds for the association to take effect
                
                # Check the proxy
                if check_proxy(ip):
                    kept_ips.append(ip)
                    kept_allocation_ids.append(allocation_id)
                    print(f"Kept IP: {ip}")
                else:
                    # Disassociate and release IP if proxy check fails
                    disassociate_elastic_ip(allocation_id)
                    release_elastic_ip(ip, allocation_id)
                    
                    # Save the failed IP prefix to a file
                    save_failed_ip_to_file(ip)

        # Adjust number of IPs to allocate in the next round
        num_ips_to_allocate = target_kept_ips - len(kept_ips)

        # Save state after each round
        save_state(num_ips_to_allocate, kept_ips, kept_allocation_ids)

        print(f"Kept IPs: {kept_ips}")
        print(f"Released IPs: {set(allocated_ips) - set(kept_ips)}")
        print(f"Number of kept IPs so far: {len(kept_ips)}")
        print(f"Number of IPs to allocate in the next round: {num_ips_to_allocate}")
        print("-----")

        # Wait 80 seconds before the next allocation round, if needed
        if num_ips_to_allocate > 0:
            countdown_timer(60)

    print(f"Final kept IPs: {kept_ips}")
    print(f"Total kept IPs: {len(kept_ips)}")

if __name__ == "__main__":
    main()
