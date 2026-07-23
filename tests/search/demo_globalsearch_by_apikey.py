import json

import requests

APIKEY = ""


def globalsearch_by_apikey(body: dict):
    # 请求URL
    url = 'https://open.feedcoopapi.com/search_api/global_search'

    # 请求头
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {APIKEY}'
    }

    try:
        # 发送 POST 请求
        response = requests.post(url, headers=headers, json=body)

        # 打印响应状态码
        print(f"Response Status Code: {response.status_code}")

        if response.status_code == 200:
            for line in response.iter_lines():
                if line:
                    line_str = line.decode('utf - 8')
                    if "invalid_request" in line_str:
                        return json.loads(response.text)
                    print(line_str)

    except Exception as e:
        print(f"Error occurred: {e!s}")


if __name__ == "__main__":
    body = {
        "Query": "北京天安门城楼",
        "DocCount": 10,
        "MaxSnippetLength": 500,
        "MaxImageCountPerDoc": 3
    }

    globalsearch_by_apikey(body=body)
