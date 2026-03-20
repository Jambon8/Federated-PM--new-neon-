import os
import subprocess
import json
from flask import Flask, render_template, request, jsonify, Response, stream_with_context

import api_helper

app = Flask(__name__)

# Configuration
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HOME_DIR = os.path.expanduser("~")

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/browse', methods=['GET'])
def browse_files():
    """Returns contents of a directory."""
    path = request.args.get('path', HOME_DIR)
    
    # Security check: prevent going above root (simple check)
    if not os.path.exists(path):
        return jsonify({"error": "Path does not exist"}), 404
        
    try:
        items = []
        # Add parent directory entry
        parent = os.path.dirname(path)
        if parent and parent != path:
             items.append({"name": "..", "path": parent, "type": "directory"})

        with os.scandir(path) as it:
            for entry in it:
                if entry.name.startswith('.'): continue # Skip hidden
                
                if entry.is_dir():
                    items.append({"name": entry.name, "path": entry.path, "type": "directory"})
                elif entry.is_file() and (entry.name.endswith('.xes') or entry.name.endswith('.xes.gz') or entry.name.endswith('.json')):
                    items.append({"name": entry.name, "path": entry.path, "type": "file"})
        
        # Sort: Directories first, then files
        items.sort(key=lambda x: (x['type'] != 'directory', x['name']))
        
        return jsonify({
            "current_path": path,
            "items": items
        })
    except PermissionError:
        return jsonify({"error": "Permission denied"}), 403
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/run', methods=['POST'])
def run_process_mining():
    data = request.json
    
    log_a = data.get('log_a')
    log_b = data.get('log_b')
    threshold = data.get('threshold', 1)
    threads = data.get('threads', 16)
    k_anon = data.get('k_anon', 0)
    mode = data.get('mode', 'local')
    network = data.get('network', None)
    use_handovers = data.get('use_handovers', False)
    is_ocel = data.get('is_ocel', False)
    flatten_type = data.get('flatten_type', 'Container')
    direct = data.get('direct', True)
    partial_orders = data.get('partial_orders', False)

    if not log_a or not log_b:
        return jsonify({"error": "Both Log A and Log B are required."}), 400

    # Command construction
    cmd = [
        "python3", "-u", "examples/run_process_mining.py", # -u for unbuffered output
        "--log-a", log_a,
        "--log-b", log_b,
        "--threshold", str(threshold),
        "--threads", str(threads),
        "--k-anon", str(k_anon),
        "--mode", mode
    ]
    
    if use_handovers:
        cmd.append("--use-handovers")
        
    if is_ocel:
        cmd.append("--is-ocel")
        if flatten_type:
            cmd.extend(["--flatten-type", flatten_type])
        
    if not direct:
        cmd.append("--no-direct")

    if partial_orders:
        cmd.extend(["--partial-orders", "1"])
    
    if mode == "local-virtual" and network:
        cmd.extend(["--network", network])
    
    print(f"Running command: {' '.join(cmd)}")
    
    def generate():
        try:
            # We must use Popen to stream stdout
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, # Merge stderr into stdout
                text=True,
                cwd=BASE_DIR,
                bufsize=1 # Line buffered
            )
            
            # Yield output line by line
            for line in process.stdout:
                yield line
            
            process.wait()
            if process.returncode != 0:
                yield f"\n[ERROR] Process exited with code {process.returncode}"
                
        except Exception as e:
            yield f"\n[EXCEPTION] {str(e)}"

    return Response(stream_with_context(generate()), mimetype='text/plain')

if __name__ == '__main__':
    app.run(debug=False, port=8000)
