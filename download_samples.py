import urllib.request
import os

base_url = "https://tonejs.github.io/audio/salamander/"
notes = [
    "A0", "C1", "Ds1", "Fs1", "A1", "C2", "Ds2", "Fs2", "A2", 
    "C3", "Ds3", "Fs3", "A3", "C4", "Ds4", "Fs4", "A4", 
    "C5", "Ds5", "Fs5", "A5", "C6", "Ds6", "Fs6", "A6", 
    "C7", "Ds7", "Fs7", "A7", "C8"
]

target_dir = os.path.join(os.path.dirname(__file__), "static", "audio", "piano")
os.makedirs(target_dir, exist_ok=True)

for note in notes:
    filename = f"{note}.mp3"
    url = f"{base_url}{filename}"
    filepath = os.path.join(target_dir, filename)
    
    if not os.path.exists(filepath):
        print(f"Downloading {filename}...")
        try:
            urllib.request.urlretrieve(url, filepath)
        except Exception as e:
            print(f"Failed to download {filename}: {e}")
    else:
        print(f"Skipping {filename}, already exists.")

print("All samples downloaded successfully.")
