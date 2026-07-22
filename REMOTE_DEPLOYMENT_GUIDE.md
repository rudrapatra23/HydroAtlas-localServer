# Remote Access & Deployment Guide

This guide explains how to run the HydroAtlas codebase on the host machine (your professor's computer) and expose it so that other computers—both inside and outside the local network—can access the frontend and backend.

---

## 1. Running the Servers to Allow External Access

By default, the Vite frontend and FastAPI backend only listen to `localhost`, which means no other computers can access them. You must run them using `0.0.0.0` to listen on all network interfaces.

###  Start the Backend (API)
Open a terminal in the `backend` folder and run:
```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```
*The backend is now accessible on port `8000`.*

###  Start the Frontend (Vite)
Open a terminal in the `frontend` folder and run:
```bash
npm run dev -- --host
```
*The frontend is now accessible on port `5173`.*

---

## 2. Connecting from INSIDE the Same Network (e.g., IITB Campus)

If a computer is connected to the exact same local network (like the campus WiFi or Ethernet), you just need the host machine's Local IP address.

### Step A: Find the Host's Local IP
1. Open **Command Prompt** or **PowerShell** on the professor's machine.
2. Type `ipconfig` and press Enter.
3. Look for **IPv4 Address** (usually under Ethernet adapter or Wireless LAN adapter).
   - *Example: `192.168.1.50` or `10.x.x.x`*

### Step B: Access from another computer
Open a web browser on any computer in the same network and type:
- **Frontend URL:** `http://<HOST_IPV4_ADDRESS>:5173` (e.g., `http://192.168.1.50:5173`)
- **Backend API Docs:** `http://<HOST_IPV4_ADDRESS>:8000/docs`

---

## 3. Connecting from OUTSIDE the Network (e.g., from Home)

If someone is trying to access the server from their home, a different campus, or via mobile data, the local IP address (`192.x.x.x`) will not work because the campus firewall blocks incoming traffic.

You will need a tunneling tool. Here are the two best options:

### Option A: Use Ngrok (Easiest for quick sharing)
Ngrok gives you a public URL that tunnels securely to the local server.
1. Download and install [ngrok](https://ngrok.com/) on the professor's machine.
2. To share the frontend publicly, run:
   ```bash
   ngrok http 5173
   ```
3. Ngrok will provide a random public URL (e.g., `https://abc-123.ngrok-free.app`). Send this URL to anyone outside the network!
*(Note: You may need a second ngrok tunnel `ngrok http 8000` if the frontend needs to talk to the backend publicly).*

### Option B: Use Tailscale (Best for long-term secure access)
Tailscale creates a private VPN network between specific computers.
1. Install [Tailscale](https://tailscale.com/) on the professor's machine and log in.
2. Install Tailscale on your home computer and log in with the **same account**.
3. Tailscale will give the professor's machine a static `100.x.x.x` IP address.
4. From your home computer, just go to `http://100.x.x.x:5173` in your browser. It ignores all campus firewalls.

