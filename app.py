import streamlit as st
from pytubefix import YouTube
from pytubefix.cli import on_progress
from openai import OpenAI
import cv2
import base64
import math
from io import BytesIO
import tempfile
from pydub import AudioSegment
import os
import pickle
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
import time

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def youtube_login():
    YOUTUBE_EMAIL = os.getenv('YOUTUBE_EMAIL')
    YOUTUBE_PASSWORD = os.getenv('YOUTUBE_PASSWORD')

    if not YOUTUBE_EMAIL or not YOUTUBE_PASSWORD:
        raise ValueError("YOUTUBE_EMAIL and YOUTUBE_PASSWORD environment variables must be set")

    # Setup Chrome options
    chrome_options = webdriver.ChromeOptions()
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--disable-dev-shm-usage")

    # Set up the Chrome WebDriver
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)

    # Open YouTube
    driver.get("https://www.youtube.com")

    # Click on the sign-in button
    sign_in_button = driver.find_element(By.XPATH, '//yt-formatted-string[text()="Sign in"]')
    sign_in_button.click()

    # Wait for the sign-in page to load
    time.sleep(3)

    # Enter the email address
    email_input = driver.find_element(By.XPATH, '//input[@type="email"]')
    email_input.send_keys(YOUTUBE_EMAIL)
    email_input.send_keys(Keys.RETURN)

    # Wait for the password input to load
    time.sleep(3)

    # Enter the password
    password_input = driver.find_element(By.XPATH, '//input[@type="password"]')
    password_input.send_keys(YOUTUBE_PASSWORD)
    password_input.send_keys(Keys.RETURN)

    # Wait for the login process to complete
    time.sleep(5)

    # Save cookies to a file
    with open("cookies.pkl", "wb") as f:
        pickle.dump(driver.get_cookies(), f)

    # Close the browser
    driver.quit()

# Call the login function
youtube_login()

# Function to download YouTube video using pytubefix
def download_video(url):
    try:
        yt = YouTube(url, on_progress_callback=on_progress, client="ANDROID_CREATOR")
        stream = yt.streams.get_highest_resolution()
        if not stream:
            st.error("No video stream found.")
            return None
        video_buffer = BytesIO()
        stream.stream_to_buffer(video_buffer)
        video_buffer.seek(0)
        return video_buffer
    except Exception as e:
        st.error(f"An error occurred while downloading the video: {e}")
        return None

# Function to split video and audio
def split_video_audio(video_buffer):
    try:
        video_buffer.seek(0)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as temp_video_file:
            temp_video_file.write(video_buffer.read())
            temp_video_file.flush()

            # Use pydub to extract and reduce the quality of the audio file
            audio = AudioSegment.from_file(temp_video_file.name)
            reduced_audio = audio.set_frame_rate(22050).set_channels(1).set_sample_width(2)  # Reduce quality

            audio_temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
            reduced_audio.export(audio_temp_file.name, format="mp3", bitrate="32k")  # Export as mp3

            return temp_video_file.name, audio_temp_file.name
    except Exception as e:
        st.error(f"An error occurred while splitting video and audio: {e}")
        return None, None

# Function to extract transcript using OpenAI Whisper
def extract_transcript(audio_file_path):
    try:
        with open(audio_file_path, "rb") as audio_file:
            transcript = client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file
            )
        return transcript.text
    except Exception as e:
        st.error(f"An error occurred while extracting the transcript: {e}")
        return None

# Function to calculate frame step
def calculate_frame_step(video_length_seconds, max_frames=200):
    if video_length_seconds < max_frames:
        return 1
    else:
        return max(1, math.ceil(video_length_seconds / max_frames))

# Function to encode frame
def encode_frame(frame):
    _, buffer = cv2.imencode(".jpg", frame)
    return base64.b64encode(buffer).decode("utf-8")

# Function to get video frames as base64
def get_video_frames(video_path):
    video = cv2.VideoCapture(video_path)
    frame_rate = video.get(cv2.CAP_PROP_FPS)
    frame_count = int(video.get(cv2.CAP_PROP_FRAME_COUNT))
    video_length_seconds = frame_count / frame_rate

    frame_step = calculate_frame_step(video_length_seconds)

    print(f"Video Frame Rate: {frame_rate}")
    print(f"Total Frame Count: {frame_count}")
    print(f"Video Length (seconds): {video_length_seconds}")
    print(f"Calculated Frame Step: {frame_step}")

    base64Frames = []
    for second in range(0, int(video_length_seconds), frame_step):
        video.set(cv2.CAP_PROP_POS_MSEC, second * 1000)
        success, frame = video.read()
        if success:
            base64Frames.append(encode_frame(frame))

    video.release()
    print(f"Number of Frames Captured: {len(base64Frames)}")
    return base64Frames


# Streamlit app
st.set_page_config(
    page_title="YouTube Video Summarizer",
    page_icon="🎥",
)

st.title("🎥YouTube Video Summarizer")
st.subheader("Paste in any YouTube video link, and the AI will summarize what it sees and hears in the video.")

# Initialize session state for URL
if "url" not in st.session_state:
    st.session_state.url = ""

st.session_state.url = st.text_input("Enter YouTube video URL:", value=st.session_state.url)

if st.session_state.url:
    with st.spinner("Downloading video..."):
        video_buffer = download_video(st.session_state.url)

    if video_buffer:
        with st.spinner("Pre-processing audio & video..."):
            video_clip_path, audio_clip_path = split_video_audio(video_buffer)

        if audio_clip_path:
            with st.spinner("Extracting transcript..."):
                transcript = extract_transcript(audio_clip_path)

            if transcript:
                with st.spinner("Ingesting video frames..."):
                    base64Frames = get_video_frames(video_clip_path)

                    # Prepare the prompt for GPT-4
                    PROMPT_MESSAGES = [
                        {
                            "role": "user",
                            "content": [
                                f"You are a video summarizer. Here is a full transcript of the video: f{transcript}.\n"
                                "These are descriptions of some of the frames from the video. Output a detailed description of what happens in the video based on the frames below and the transcript given to you above.",
                                *map(lambda x: {"type": "image_url",
                                                "image_url": {"url": f"data:image/jpeg;base64,{x}", "detail": "low"}},
                                     base64Frames),
                            ],
                        },
                    ]

                    # Define parameters for the API request
                    params = {
                        "model": "gpt-4o-mini",
                        "messages": PROMPT_MESSAGES,
                        "max_tokens": 2000,
                    }

                    # Send the request to GPT-4
                    result = client.chat.completions.create(**params)
                    description = result.choices[0].message.content

                    st.write("Video Description:")
                    st.write(description)

