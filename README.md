# gemini_test_apikey
批量测试gmini的apikey是否可用

### 使用方法
新建 `test.txt` 文件 将apikey每行一个
```bash
python .\gemini.py .\test.txt  --concurrency 20 --timeout 10 --output results.csv
```
