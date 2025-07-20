#!/usr/bin/env python3
"""
Ping Monitor: A tool to monitor network connectivity by pinging multiple IP addresses
or domains concurrently. Displays real-time latency, packet loss, and statistics with
a colorful terminal interface.

Features:
- Asynchronous pinging of multiple IPs/domains.
- Real-time min, max, avg, median latency, and packet loss.
- Colored output and ASCII bar graphs for ping times.
- Alerts for high latency (>200ms) or packet loss (>20%).
- Cross-platform support (Linux, macOS, Windows).
- Graceful Ctrl+C handling.
- Ping IPs from a user-specified file with -f/--file.

Usage:
    ./ping_monitor.py 8.8.8.8 google.com
    ./ping_monitor.py -f troubleshoot_ips.txt
    ./ping_monitor.py 8.8.8.8 -f my_ips.txt

See README.md for full documentation.
"""
import asyncio
import platform
import re
import argparse
import ipaddress
import os
import time
from collections import deque
import statistics
import socket

__version__ = "1.1.0"

# Configuration settings
MAX_HISTORY = 50  # Number of pings to keep for statistics
UPDATE_INTERVAL = 1.0  # Seconds between pings
TIMEOUT = 2.0  # Ping timeout in seconds
LOSS_THRESHOLD = 20.0  # Alert if packet loss exceeds 20%
LATENCY_THRESHOLD = 200.0  # Alert if ping time exceeds 200ms

# ANSI color codes
GREEN = '\033[92m'
YELLOW = '\033[93m'
RED = '\033[91m'
BLUE = '\033[94m'
BOLD = '\033[1m'
RESET = '\033[0m'

# ASCII art for headers
PING_MONITOR_ASCII = """
███╗   ███╗ ██████╗ ███╗   ██╗██╗████████╗ ██████╗ ██████╗ 
████╗ ████║██╔═══██╗████╗  ██║██║╚══██╔══╝██╔═══██╗██╔══██╗
██╔████╔██║██║   ██║██╔██╗ ██║██║   ██║   ██║   ██║██████╔╝
██║╚██╔╝██║██║   ██║██║╚██╗██║██║   ██║   ██║   ██║██╔══██╗
██║ ╚═╝ ██║╚██████╔╝██║ ╚████║██║   ██║   ╚██████╔╝██║  ██║
╚═╝     ╚═╝ ╚═════╝ ╚═╝  ╚═══╝╚═╝   ╚═╝    ╚═════╝ ╚═╝  ╚═╝
"""

PACKET_STATISTICS_ASCII = """
██████╗  █████╗  ██████╗██╗  ██╗███████╗████████╗███████╗
██╔══██╗██╔══██╗██╔════╝██║ ██╔╝██╔════╝╚══██╔══╝██╔════╝
██████╔╝███████║██║     █████╔╝ █████╗     ██║   ███████╗
██╔═══╝ ██╔══██║██║     ██╔═██╗ ██╔══╝     ██║   ╚════██║
██║     ██║  ██║╚██████╗██║  ██╗███████╗   ██║   ███████║
╚═╝     ╚═╝  ╚═╝ ╚═════╝╚═╝  ╚═╝╚══════╝   ╚═╝   ╚══════╝
"""

def validate_ip(ip_or_domain):
    """Validate an IP address or resolve a domain to an IP.

    Args:
        ip_or_domain (str): IP address or domain name.

    Returns:
        str: Resolved IP address, or None if invalid.
    """
    try:
        ipaddress.ip_address(ip_or_domain)
        return ip_or_domain
    except ValueError:
        try:
            resolved_ip = socket.gethostbyname(ip_or_domain)
            return resolved_ip
        except socket.gaierror:
            return None

def read_ips_from_file(filename):
    """Read IPs or domains from a specified file.

    Args:
        filename (str): Path to the IP file.

    Returns:
        list: List of valid IPs or domains from the file.
    """
    ips = []
    try:
        if not os.path.exists(filename):
            print(f"Error: File '{filename}' not found")
            return ips
        with open(filename, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    if validate_ip(line):
                        ips.append(line)
                    else:
                        print(f"Warning: Invalid IP or domain in {filename}: '{line}' - skipping")
        return ips
    except Exception as e:
        print(f"Error reading {filename}: {e}")
        return []

def parse_arguments():
    """Parse command-line arguments.

    Returns:
        argparse.Namespace: Parsed arguments with IP mappings.
    """
    parser = argparse.ArgumentParser(description="Ping multiple IP addresses or domains.")
    parser.add_argument("ips", nargs="*", help="IP addresses or domains to ping")
    parser.add_argument("-i", "--interval", type=float, default=UPDATE_INTERVAL, help="Seconds between pings")
    parser.add_argument("-t", "--timeout", type=float, default=TIMEOUT, help="Ping timeout in seconds")
    parser.add_argument("-c", "--count", type=int, default=0, help="Number of pings (0 = forever)")
    parser.add_argument("--no-color", action="store_true", help="Turn off colored output")
    parser.add_argument("-f", "--file", help="File containing IPs or domains to ping (one per line)")
    args = parser.parse_args()

    ip_mappings = []
    input_ips = args.ips
    if args.file:
        file_ips = read_ips_from_file(args.file)
        input_ips.extend(file_ips)

    if not input_ips:
        parser.error("No IPs provided. Use IP arguments or -f/--file with a valid IP file")

    for ip_or_domain in input_ips:
        resolved_ip = validate_ip(ip_or_domain)
        if resolved_ip:
            ip_mappings.append((ip_or_domain, resolved_ip))
            if resolved_ip != ip_or_domain:
                print(f"Resolved {ip_or_domain} to {resolved_ip}")
        else:
            print(f"Warning: Invalid IP or domain '{ip_or_domain}' - skipping")
    if not ip_mappings:
        parser.error("No valid IP addresses or domains provided")
    args.ip_mappings = ip_mappings
    return args

async def ping_ip(ip, timeout):
    """Ping an IP and return the response time.

    Args:
        ip (str): IP address to ping.
        timeout (float): Timeout in seconds.

    Returns:
        float: Ping time in milliseconds, or None if failed.
    """
    param = '-n' if platform.system().lower() == 'windows' else '-c'
    timeout_param = '-w' if platform.system().lower() == 'windows' else '-W'
    cmd = f"ping {param} 1 {timeout_param} {int(timeout)} {ip}"
    try:
        process = await asyncio.create_subprocess_shell(
            cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout + 1)
        output = stdout.decode()
        match = re.search(r"time[<=](\d+\.?\d*)", output)
        if match:
            return float(match.group(1))
        return None
    except asyncio.TimeoutError:
        print(f"Ping timeout for {ip}")
        return None
    except Exception as e:
        print(f"Error pinging {ip}: {e}")
        return None

async def ping_all_ips(ips, timeout):
    """Ping all IPs concurrently.

    Args:
        ips (list): List of IP addresses.
        timeout (float): Timeout in seconds.

    Returns:
        dict: Mapping of IPs to ping times (or None).
    """
    tasks = [ping_ip(ip, timeout) for ip in ips]
    results = await asyncio.gather(*tasks)
    return {ip: result for ip, result in zip(ips, results)}

def calculate_statistics(history):
    """Calculate statistics from ping history.

    Args:
        history (deque): History of ping times (None for failed pings).

    Returns:
        dict: Statistics (min, max, avg, median, loss).
    """
    valid_pings = [x for x in history if x is not None]
    total_pings = len(history)
    stats = {'min': None, 'max': None, 'avg': None, 'median': None, 'loss': 100.0}
    if valid_pings:
        stats['min'] = min(valid_pings)
        stats['max'] = max(valid_pings)
        stats['avg'] = sum(valid_pings) / len(valid_pings)
        stats['median'] = statistics.median(valid_pings)
        stats['loss'] = (1 - len(valid_pings) / total_pings) * 100
    return stats

def get_time_color(ping_time, use_color):
    """Get color for ping time.

    Args:
        ping_time (float): Ping time in milliseconds (or None).
        use_color (bool): Whether to use colors.

    Returns:
        str: ANSI color code.
    """
    if not use_color or ping_time is None:
        return ''
    if ping_time < 50:
        return GREEN
    elif ping_time < 100:
        return YELLOW
    else:
        return RED

def get_loss_color(loss, use_color):
    """Get color for packet loss.

    Args:
        loss (float): Packet loss percentage.
        use_color (bool): Whether to use colors.

    Returns:
        str: ANSI color code.
    """
    if not use_color:
        return ''
    if loss < 5:
        return GREEN
    elif loss < 20:
        return YELLOW
    else:
        return RED

def get_ping_bar(ping_time, use_color, max_length=20):
    """Generate an ASCII bar for ping time.

    Args:
        ping_time (float): Ping time in milliseconds (or None).
        use_color (bool): Whether to use colors.
        max_length (int): Maximum bar length.

    Returns:
        str: Colored ASCII bar.
    """
    if ping_time is None:
        return "N/A"
    bar_length = min(int(ping_time / 5), max_length)
    if ping_time > 0 and bar_length == 0:
        bar_length = 1
    bar = "||" * bar_length
    color = get_time_color(ping_time, use_color)
    return f"{color}{BOLD if use_color else ''}{bar}{RESET if use_color else ''}"

def clear_screen():
    """Clear the terminal screen."""
    os.system('cls' if platform.system().lower() == 'windows' else 'clear')

def print_ping_results(ping_data, histories, use_color, ip_mappings):
    """Print a table with ping results and statistics.

    Args:
        ping_data (dict): Latest ping times (IP -> time).
        histories (dict): Ping history and packet counts.
        use_color (bool): Whether to use colored output.
        ip_mappings (list): List of (original, resolved) IP tuples.
    """
    clear_screen()
    terminal_width = os.get_terminal_size().columns
    current_time = time.strftime("%Y-%m-%d %H:%M:%S")

    print(f"{BLUE if use_color else ''}{BOLD if use_color else ''}{PING_MONITOR_ASCII.strip()}{RESET if use_color else ''}")
    print(f"{BOLD if use_color else ''} {current_time} {RESET if use_color else ''}")
    print("=" * terminal_width)

    mapping_dict = {original: resolved for original, resolved in ip_mappings}
    ip_display_lengths = [len(original if original == resolved else f"{original} ({resolved})")
                         for original, resolved in ip_mappings]
    ip_column_width = max(ip_display_lengths, default=15, key=lambda x: x if x > 15 else 15) + 2
    col_width = 10
    ping_graph_width = 20

    print(f"{'IP ADDRESS':<{ip_column_width}} {'LAST':<{col_width}} {'MIN':<{col_width}} {'AVG':<{col_width}} "
          f"{'MAX':<{col_width}} {'MEDIAN':<{col_width}} {'LOSS %':<{col_width}} {'PING GRAPH':<{ping_graph_width}}")
    print("-" * terminal_width)

    for ip in sorted(ping_data.keys()):
        stats = calculate_statistics(histories[ip]['pings'])
        current_time = ping_data[ip]
        time_str = f"{current_time:.1f}ms" if current_time is not None else "timeout"
        min_val = f"{stats['min']:.1f}ms" if stats['min'] is not None else "N/A"
        avg_val = f"{stats['avg']:.1f}ms" if stats['avg'] is not None else "N/A"
        max_val = f"{stats['max']:.1f}ms" if stats['max'] is not None else "N/A"
        median_val = f"{stats['median']:.1f}ms" if stats['median'] is not None else "N/A"
        loss_str = f"{stats['loss']:.1f}%"
        ping_bar = get_ping_bar(current_time, use_color)

        time_color = get_time_color(current_time, use_color)
        time_str_colored = f"{time_color}{time_str:<{col_width}}{RESET if use_color else ''}"
        loss_color = get_loss_color(stats['loss'], use_color)
        loss_str_colored = f"{loss_color}{loss_str:<{col_width}}{RESET if use_color else ''}"
        resolved_ip = mapping_dict[ip]
        display_ip = ip if ip == resolved_ip else f"{ip} ({resolved_ip})"
        ip_colored = f"{BLUE if use_color else ''}{display_ip:<{ip_column_width}}{RESET if use_color else ''}"

        print(f"{ip_colored} {time_str_colored} {min_val:<{col_width}} {avg_val:<{col_width}} "
              f"{max_val:<{col_width}} {median_val:<{col_width}} {loss_str_colored} {ping_bar:<{ping_graph_width}}")

        if stats['loss'] > LOSS_THRESHOLD:
            print(f"{RED if use_color else ''}Warning: High packet loss for {ip}: {stats['loss']:.1f}%{RESET if use_color else ''}")
        if current_time is not None and current_time > LATENCY_THRESHOLD:
            print(f"{RED if use_color else ''}Warning: High latency for {ip}: {current_time:.1f}ms{RESET if use_color else ''}")

    print("=" * terminal_width)
    print("\n")
    print(f"{GREEN if use_color else ''}{BOLD if use_color else ''}{PACKET_STATISTICS_ASCII.strip()}{RESET if use_color else ''}")
    print("-" * terminal_width)
    print(f"{'IP ADDRESS':<{ip_column_width}} {'TOTAL':<{col_width}} {'SUCCESS':<{col_width}} {'FAILED':<{col_width}}")
    print("-" * terminal_width)
    for ip in sorted(ping_data.keys()):
        total = histories[ip]['total_packets']
        success = histories[ip]['success_packets']
        failed = histories[ip]['failed_packets']
        display_ip = ip if ip == mapping_dict[ip] else f"{ip} ({mapping_dict[ip]})"
        print(f"{display_ip:<{ip_column_width}} {total:<{col_width}} {success:<{col_width}} {failed:<{col_width}}")
    print("-" * terminal_width)
    print(f"Update interval: {UPDATE_INTERVAL}s | Press Ctrl+C to exit")

async def main():
    """Main function to run the ping monitor."""
    args = parse_arguments()
    print(f"Starting ping monitor for {len(args.ip_mappings)} IPs:")
    for original, resolved in args.ip_mappings:
        print(f"  - {original}")
    print("Initializing...")
    histories = {original: {'pings': deque(maxlen=MAX_HISTORY), 'total_packets': 0, 'success_packets': 0, 'failed_packets': 0} for original, _ in args.ip_mappings}
    
    loop = asyncio.get_running_loop()
    tasks = []

    try:
        ping_count = 0
        while args.count == 0 or ping_count < args.count:
            resolved_ips = [resolved for _, resolved in args.ip_mappings]
            ping_data = await ping_all_ips(resolved_ips, args.timeout)
            ping_data_mapped = {args.ip_mappings[i][0]: result for i, result in enumerate(ping_data.values())}
            for original, result in ping_data_mapped.items():
                histories[original]['pings'].append(result)
                histories[original]['total_packets'] += 1
                if result is not None:
                    histories[original]['success_packets'] += 1
                else:
                    histories[original]['failed_packets'] += 1
            print_ping_results(ping_data_mapped, histories, not args.no_color, args.ip_mappings)
            ping_count += 1
            tasks.append(loop.create_task(asyncio.sleep(args.interval)))
            await tasks[-1]
    except KeyboardInterrupt:
        print("\nStopping ping monitor...")
        # Cancel all running tasks
        for task in tasks:
            task.cancel()
        # Wait for tasks to be cancelled
        await asyncio.gather(*tasks, return_exceptions=True)
        # Properly shut down async generators
        await loop.shutdown_asyncgens()
        print("Stopped by user. Exiting...")
    # Remove the finally block to avoid duplicate loop shutdown

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nExiting due to user interrupt.")
