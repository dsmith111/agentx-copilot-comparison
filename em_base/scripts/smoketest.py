from azure.storage.filedatalake import DataLakeServiceClient
from azure.core.credentials import AzureNamedKeyCredential

account_name = "devstoreaccount1"
account_key = "localdevkey"
account_url = "http://127.0.0.1:10004/devstoreaccount1"

service = DataLakeServiceClient(
    account_url=account_url,
    credential=AzureNamedKeyCredential(account_name, account_key),
)

fs = service.create_file_system("demo")
directory = fs.create_directory("events/2026/05/06")
file_client = directory.create_file("sample.txt")

data = b"hello local adls gen2"
file_client.append_data(data, offset=0, length=len(data))
file_client.flush_data(len(data))

downloaded = file_client.download_file().readall()
assert downloaded == data

paths = list(fs.get_paths(path="events", recursive=True))
print([p.name for p in paths])