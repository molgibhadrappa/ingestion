pip install selenium google-cloud-storage webdriver-manager

import os
import time
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.options import Options
from google.cloud import storage
import shutil

# Google Cloud Storage bucket name
BUCKET_NAME = 'your-gcs-bucket-name'  # Replace with your bucket name
GCS_UPLOAD_PATH = 'drug_shortages.csv'  # File path in the GCS bucket

# Set Chrome options for headless browser
chrome_options = Options()
chrome_options.add_argument("--headless")
chrome_options.add_argument("--no-sandbox")

# Set up the ChromeDriver with Service and ChromeOptions
driver_service = Service(ChromeDriverManager().install())
driver = webdriver.Chrome(service=driver_service, options=chrome_options)

# Set the path for the file to be downloaded locally
download_dir = "/tmp"  # Using /tmp directory, Cloud Run's writable space

if not os.path.exists(download_dir):
    os.makedirs(download_dir)

# Set download preferences to save the file in the specified directory
chrome_options.add_experimental_option("prefs", {
    "download.default_directory": download_dir,
    "download.prompt_for_download": False,
    "download.directory_upgrade": True,
    "safebrowsing.enabled": True
})

# Initialize the browser again with updated options
driver = webdriver.Chrome(service=driver_service, options=chrome_options)

# Navigate to the FDA Drug Shortages page
url = 'https://dps.fda.gov/drugshortages'
driver.get(url)

# Wait for the page to load completely
time.sleep(5)

try:
    # Find the "Download Current Drug Shortages" button by its text
    download_button = driver.find_element(By.PARTIAL_LINK_TEXT, 'Download Current Drug Shortages')
    
    # Click the download button
    download_button.click()

    # Wait for the file to download (adjust the time based on your file size)
    time.sleep(10)
    print(f"File downloaded successfully to {download_dir}.")
    
except Exception as e:
    print(f"Error: {e}")

# Close the browser
driver.quit()

# After downloading, upload the file to Google Cloud Storage
def upload_to_gcs():
    # Initialize the GCS client
    client = storage.Client()

    # Specify the bucket and file
    bucket = client.get_bucket(BUCKET_NAME)
    blob = bucket.blob(GCS_UPLOAD_PATH)

    # Upload the file
    local_file_path = os.path.join(download_dir, 'drug_shortages.csv')  # Specify the downloaded file path
    blob.upload_from_filename(local_file_path)
    print(f"File uploaded successfully to GCS bucket: {BUCKET_NAME}/{GCS_UPLOAD_PATH}")

# Call the upload function
upload_to_gcs()



import requests
from google.cloud import storage

# API URL and Token from the response
api_url = 'https://dps-admin.fda.gov/drugshortages/api/products?download=dshors'
token = 'eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiIsImp0aSI6IjNkYmZlNTg4NTNjYzI5NDA3NzYyMWQ2MmFkM2FmM2RjNjY3NWI1ZWYzZGM3OWEzMDY5NDhiNmQ2Yzk3YmE0YjUwNTAzMjk0OWYwN2MwOGFiIn0.eyJhdWQiOiItYzlRSFl6WFpQLXItMlNyOTNJMWx4dmk4Z3BqalQyLWV1NjBwNF82aWs4IiwianRpIjoiM2RiZmU1ODg1M2NjMjk0MDc3NjIxZDYyYWQzYWYzZGM2Njc1YjVlZjNkYzc5YTMwNjk0OGI2ZDZjOTdiYTRiNTA1MDMyOTQ5ZjA3YzA4YWIiLCJpYXQiOjE3NDQ4MjY2OTMsIm5iZiI6MTc0NDgyNjY5MywiZXhwIjoxNzQ0ODMwMjkzLjc0ODUzOTksInN1YiI6IjIiLCJzY29wZSI6WyJhdXRoZW50aWNhdGVkIiwiZHNob3JfYXBwIl19.XhppTWgBC4NRGWtBGleDVwQi5XkbDMUCitXasZN6v4yHU6VIqzuB06sfi7IN5ZTW9zedu2aRc8Fvw-WsJYgsFiLcXvE82HhAWARyQzSiE9KjUxui8sCKdNQ-CpKEr2rKnExo_0zTU0moAaUwqvPnFfCjTaUfKktg_G884EaFy49guKuxEOEmpUstpGBjsECHHzeQ4mVOHKxegEPMM98Gv42BvhHn_xPWcPJYXpg1aj0JxCn4JXYdG74veh1-NPXE50AlgVSbmyL4LW_J5y03MRZLcRDrt7Resb9Ge09TxNj5S196_BduaxmCD5TbfRgou4XpO3XEH8jEyggHpsEsSkcHw2oJPr26LKdj3SBuxW-soSwQWqjJIKKdGFcUg_U6CqS_xUw5jgELwBKI4cLc5zhZW-PL0B5J9AKnANV7pC6BpebU84wt1L-6JMpCWDp6rF0nV2udtawxhfD7effSVCr53L6uI0Iv3G60-xp4L_MaxIrIqzNaUOdl99FxdV3b2knlGRysCygW4Pp1hkxHyjBn8JlhJGFnQwB-ZUe_6fNJh1QMHHLvQ2v0HckPLM_qUdyWFFS-cM9af7j4yDqURFcIGAPNc0Q--ULaPnrXmyBzby-C9xRLO3xGIQQX9T39NeFpwAjKzbots3yvhnVYHQ1zA7KvvbwCQtztv1TjlLI'

# Headers with Authorization token
headers = {
    'Authorization': f'Bearer {token}',
}

# Send GET request to the API
response = requests.get(api_url, headers=headers)

# Check if the request was successful
if response.status_code == 200:
    # Save the file locally (Cloud Run uses /tmp directory for storage)
    local_file_path = '/tmp/drug_shortages.csv'

    # Save the content to a file
    with open(local_file_path, 'wb') as f:
        f.write(response.content)
    print(f"File downloaded successfully to {local_file_path}")

    # Upload to Google Cloud Storage
    bucket_name = 'your-gcs-bucket-name'  # Replace with your bucket name
    upload_to_gcs(local_file_path, bucket_name)
else:
    print(f"Failed to download the file. Status code: {response.status_code}")

# Function to upload file to GCS
def upload_to_gcs(local_file_path, bucket_name):
    # Initialize Google Cloud Storage client
    client = storage.Client()

    # Specify the GCS bucket and destination blob
    bucket = client.bucket(bucket_name)
    blob = bucket.blob('drug_shortages.csv')  # Path in GCS

    # Upload the file
    blob.upload_from_filename(local_file_path)
    print(f"File uploaded successfully to GCS bucket: gs://{bucket_name}/drug_shortages.csv")

