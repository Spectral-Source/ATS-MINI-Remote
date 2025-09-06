# ATS-MINI Receiver Remote Control

A web-based remote control for the ATS-MINI portable shortwave radio receiver. This project uses a Flask backend and a responsive web interface to let you control your radio from any device on your local network, including a PC or a mobile phone.



<img width="841" height="586" alt="image" src="https://github.com/user-attachments/assets/03178d70-252d-4325-ae72-16a1c75ca535" />

---

### ✨ Features

* **Live Monitoring:** Displays real-time radio data like frequency, band, mode, RSSI, SNR, and volume.
* **Intuitive UI:** A clean interface with a clickable/touch-enabled VFO knob for smooth tuning.
* **Full Control:** Dedicated buttons to change volume, band, mode, step size, and more.
* **Multi-Platform Support:** The web interface is responsive, working on both desktop browsers and mobile touchscreens.
* **Network Access:** The Flask server allows remote access from any device on the same local network.

---


### 🛠️ Getting Started

#### Prerequisites

* Python 3.x
* An ATS-MINI receiver connected to your computer via USB.
* Required Python libraries: **Flask** and **pyserial**.

#### Installation

1.  Clone this repository or download the source code files.
2.  Open your terminal in the project directory.
3.  Install the required libraries:
    ```bash
    pip install Flask pyserial
    ```

#### Running the Application

1.  Connect your ATS-MINI receiver to your computer with a USB cable.
2.  Run the Flask server:
    ```bash
    python backend.py
    ```
3.  Open your web browser and navigate to `http://localhost:5000`. If you're using another device, replace `localhost` with your computer's local IP address (e.g., `http://192.168.1.10:5000`).

---

### 📝 Project Files

* `backend.py`: The Flask server that handles serial communication with the radio and serves the web interface.
* `templates/index.html`: The complete frontend file, containing the UI structure, styling, and all of the JavaScript logic.

 
 ###  TODO

* **Audio Streaming:** Tunnel the receiver's audio output into the web interface. (AUX Socket)
