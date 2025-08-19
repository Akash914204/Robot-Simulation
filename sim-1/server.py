import asyncio
import json
import websockets
from flask import Flask, request, jsonify
import threading

app = Flask(__name__)

# --- CORS ---
@app.after_request
def add_cors_headers(resp):
    resp.headers['Access-Control-Allow-Origin'] = '*'
    resp.headers['Access-Control-Allow-Methods'] = 'GET,POST,OPTIONS'
    resp.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    return resp

# ---------------------------
# Globals
# ---------------------------
connected = set()
async_loop = None
collision_count = 0
last_capture_image = None
latest_pose = None
goal_reached_flag = False
FLOOR_HALF = 50

# ---------------------------
# Helper: Corner to coordinates
# ---------------------------
def corner_to_coords(corner: str, margin=5):
    c = corner.upper()
    x = FLOOR_HALF - margin if "E" in c else -(FLOOR_HALF - margin)
    z = FLOOR_HALF - margin if ("S" in c or "B" in c) else -(FLOOR_HALF - margin)
    if c in ("NE","EN","TR"): x,z = FLOOR_HALF-margin,-(FLOOR_HALF-margin)
    if c in ("NW","WN","TL"): x,z = -(FLOOR_HALF-margin),-(FLOOR_HALF-margin)
    if c in ("SE","ES","BR"): x,z = FLOOR_HALF-margin,FLOOR_HALF-margin
    if c in ("SW","WS","BL"): x,z = -(FLOOR_HALF-margin),FLOOR_HALF-margin
    return {"x": x, "y":0, "z":z}

# ---------------------------
# WebSocket handler
# ---------------------------
async def ws_handler(websocket, path=None):
    global collision_count, last_capture_image, latest_pose, goal_reached_flag
    connected.add(websocket)
    try:
        async for message in websocket:
            try:
                data = json.loads(message)
                if isinstance(data, dict):
                    t = data.get("type")
                    if t=="collision" and data.get("collision"):
                        collision_count += 1
                    elif t=="capture_image_response" and "image" in data:
                        last_capture_image = data.get("image")
                    elif t=="pose" and "position" in data:
                        latest_pose = data.get("position")
                    elif t=="goal_reached":
                        goal_reached_flag = True
            except Exception:
                pass
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        connected.remove(websocket)

def broadcast(msg: dict):
    if not connected:
        return False
    for ws in list(connected):
        asyncio.run_coroutine_threadsafe(ws.send(json.dumps(msg)), async_loop)
    return True

# ---------------------------
# Flask endpoints
# ---------------------------
@app.route('/move', methods=['POST'])
def move():
    data = request.get_json()
    if not data or 'x' not in data or 'z' not in data:
        return jsonify({'error':'Missing x/z'}),400
    msg={"command":"move","target":{"x":data['x'],"y":0,"z":data['z']}}
    if not broadcast(msg): return jsonify({'error':'No simulators connected'}),400
    return jsonify({'status':'move command sent','command':msg})

@app.route('/move_rel', methods=['POST'])
def move_rel():
    data=request.get_json()
    if not data or 'turn' not in data or 'distance' not in data:
        return jsonify({'error':'Missing turn/distance'}),400
    msg={"command":"move_relative","turn":data['turn'],"distance":data['distance']}
    if not broadcast(msg): return jsonify({'error':'No simulators connected'}),400
    return jsonify({'status':'move relative sent','command':msg})

@app.route('/stop', methods=['POST'])
def stop():
    msg={"command":"stop"}
    if not broadcast(msg): return jsonify({'error':'No simulators connected'}),400
    return jsonify({'status':'stop sent','command':msg})

@app.route('/capture', methods=['POST'])
def capture():
    msg={"command":"capture_image"}
    if not broadcast(msg): return jsonify({'error':'No simulators connected'}),400
    return jsonify({'status':'capture sent','command':msg})

@app.route('/goal', methods=['POST'])
def set_goal():
    data=request.get_json() or {}
    if 'corner' in data:
        pos=corner_to_coords(str(data['corner']))
    elif 'x' in data and 'z' in data:
        pos={"x":float(data['x']),"y":float(data.get('y',0)),"z":float(data['z'])}
    else:
        return jsonify({'error':'Provide corner or x/z'}),400
    msg={"command":"set_goal","position":pos}
    if not broadcast(msg): return jsonify({'error':'No simulators connected'}),400
    return jsonify({'status':'goal set','goal':pos})

@app.route('/obstacles/positions', methods=['POST'])
def set_obstacle_positions():
    data=request.get_json() or {}
    positions=data.get('positions')
    if not isinstance(positions,list) or not positions:
        return jsonify({'error':'Provide positions list'}),400
    norm=[]
    for p in positions:
        if not isinstance(p,dict) or 'x' not in p or 'z' not in p:
            return jsonify({'error':'Each position needs x/z'}),400
        norm.append({"x":float(p['x']),"y":float(p.get('y',2)),"z":float(p['z'])})
    msg={"command":"set_obstacles","positions":norm}
    if not broadcast(msg): return jsonify({'error':'No simulators connected'}),400
    return jsonify({'status':'obstacles updated','count':len(norm)})

@app.route('/obstacles/motion', methods=['POST'])
def set_obstacle_motion():
    data=request.get_json() or {}
    if 'enabled' not in data: return jsonify({'error':'Missing enabled'}),400
    msg={"command":"set_obstacle_motion",
         "enabled":bool(data['enabled']),
         "speed":float(data.get('speed',0.05)),
         "velocities":data.get('velocities'),
         "bounds":data.get('bounds',{"minX":-45,"maxX":45,"minZ":-45,"maxZ":45}),
         "bounce":bool(data.get('bounce',True))}
    if not broadcast(msg): return jsonify({'error':'No simulators connected'}),400
    return jsonify({'status':'obstacle motion updated','config':msg})

@app.route('/last_capture', methods=['GET'])
def get_last_capture():
    if last_capture_image is None: return jsonify({'status':'no_image'}),404
    return jsonify({'image':last_capture_image})

@app.route('/goal_status', methods=['GET'])
def get_goal_status():
    return jsonify({'goal_reached':bool(goal_reached_flag)})

@app.route('/clear_goal', methods=['POST'])
def clear_goal():
    global goal_reached_flag
    goal_reached_flag=False
    return jsonify({'status':'cleared'})

@app.route('/pose', methods=['GET'])
def get_pose():
    if latest_pose is None: return jsonify({'status':'no_pose'}),404
    return jsonify({'pose':latest_pose})

@app.route('/collisions', methods=['GET'])
def get_collisions():
    return jsonify({'count':collision_count})

@app.route('/reset', methods=['POST'])
def reset():
    global collision_count
    collision_count=0
    broadcast({"command":"reset"})
    return jsonify({'status':'reset','collisions':collision_count})

# ---------------------------
# Flask thread
# ---------------------------
def start_flask():
    app.run(port=5000)

# ---------------------------
# Main async WebSocket
# ---------------------------
async def main():
    global async_loop
    async_loop=asyncio.get_running_loop()
    ws_server=await websockets.serve(ws_handler,"localhost",8080)
    print("WebSocket server started on ws://localhost:8080")
    await ws_server.wait_closed()

# ---------------------------
# Entry
# ---------------------------
if __name__=="__main__":
    flask_thread=threading.Thread(target=start_flask,daemon=True)
    flask_thread.start()
    asyncio.run(main())
