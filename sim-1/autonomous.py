import requests, time, base64, random
import numpy as np, cv2, math

BASE="http://127.0.0.1:5000"
OBSTACLE_THRESHOLD=1200
FORWARD_INTERVAL=0.5

# Set initial pose
requests.post(f"{BASE}/set_pose", json={"position":{"x":0,"y":0,"z":0},"orientation":{"yaw":0}})

# ---- Parameters ----
AVOID_STEP_MIN=3
AVOID_STEP_MAX=6
AVOID_COOLDOWN=0.2
BACKUP_DISTANCE=-1.5
STUCK_TIMEOUT=10

# ---- Enable moving obstacles ----
def enable_moving_obstacles(speed=0.08,bounce=True):
    requests.post(f"{BASE}/obstacles/motion", json={"enabled":True,"speed":speed,"bounce":bounce})
    print("[Obstacles] Moving obstacles enabled")

# ---- Pose handling ----
def get_pose(timeout=10):
    start_time = time.time()
    while True:
        try:
            r = requests.get(f"{BASE}/pose")
            if r.status_code == 200 and 'pose' in r.json():
                return r.json()['pose']
        except:
            pass
        if time.time() - start_time > timeout:
            print("[Fallback] Pose not available, using default")
            return {"x": 0.0, "y": 0.0, "z": 0.0}
        print("[Waiting] Pose not available yet...")
        time.sleep(0.2)


def distance_to_goal(goal, pose):
    dx=goal['x']-pose['x']
    dz=goal['z']-pose['z']
    return math.sqrt(dx*dx+dz*dz)

def angle_to_goal(goal, pose):
    dx=goal['x']-pose['x']
    dz=goal['z']-pose['z']
    return math.degrees(math.atan2(dz,dx))

# ---- Movement ----
def move_toward_goal(goal):
    requests.post(f"{BASE}/move", json={"x":float(goal['x']),"z":float(goal['z'])})

def trigger_capture_and_get_image():
    requests.post(f"{BASE}/capture")
    start=time.time()
    while time.time()-start<5:
        r=requests.get(f"{BASE}/last_capture")
        if r.status_code==200:
            img_b64=r.json().get("image")
            if img_b64:
                b=base64.b64decode(img_b64+"="*((4-len(img_b64)%4)%4))
                arr=np.frombuffer(b,dtype=np.uint8)
                return cv2.imdecode(arr,cv2.IMREAD_COLOR)
        time.sleep(0.1)
    return None

def detect_green_obstacle_ahead(img):
    hsv=cv2.cvtColor(img,cv2.COLOR_BGR2HSV)
    mask=cv2.inRange(hsv,np.array([40,40,40]),np.array([90,255,255]))
    h,w=mask.shape
    center=mask[int(h*0.45):int(h*0.85),int(w*0.35):int(w*0.65)]
    return cv2.countNonZero(center)

def avoid_obstacle(goal):
    pose=get_pose()
    print("[Avoid] Obstacle detected, moving slightly aside")
    # Move a small random step sideways while facing goal
    step=random.uniform(1,3)
    requests.post(f"{BASE}/move_rel", json={"turn":random.choice([-30,30]),"distance":step})
    time.sleep(AVOID_COOLDOWN)
    move_toward_goal(goal)

# ---- Autonomous loop ----
def autonomous_run():
    enable_moving_obstacles()
    corners=["NE","NW","SE","SW"]
    corner=random.choice(corners)
    print("Setting goal:",corner)
    goal=requests.post(f"{BASE}/goal", json={"corner":corner}).json().get("goal")
    move_toward_goal(goal)
    
    last_progress=time.time()
    while True:
        pose=get_pose()
        dist=distance_to_goal(goal,pose)
        if dist<1.0:
            print("✅ Goal reached!")
            break
        
        img=trigger_capture_and_get_image()
        if img is not None and detect_green_obstacle_ahead(img)>OBSTACLE_THRESHOLD:
            avoid_obstacle(goal)
        else:
            move_toward_goal(goal)
        
        # Stuck detection
        if time.time()-last_progress>STUCK_TIMEOUT:
            print("[Stuck] Resetting")
            requests.post(f"{BASE}/reset")
            move_toward_goal(goal)
            last_progress=time.time()
        else:
            last_progress=time.time()
        time.sleep(FORWARD_INTERVAL)

if __name__=="__main__":
    autonomous_run()
