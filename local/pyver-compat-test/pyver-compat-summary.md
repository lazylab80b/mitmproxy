## Runtime versions
| Python  | gzip   | zlib(build) | zlib(runtime) |
| ------- | ------ | ----------- | ------------- |
| 3.8.20  | stdlib | 1.2.13      | 1.2.13        |
| 3.9.23  | stdlib | 1.2.13      | 1.2.13        |
| 3.10.18 | stdlib | 1.2.13      | 1.2.13        |
| 3.11.13 | stdlib | 1.2.13      | 1.2.13        |
| 3.12.11 | stdlib | 1.2.13      | 1.2.13        |
| 3.13.6  | stdlib | 1.2.13      | 1.2.8         |
| 3.13.6  | stdlib | 1.2.13      | 1.2.11        |
| 3.13.6  | stdlib | 1.2.13      | 1.2.13        |
| 3.13.6  | stdlib | 1.2.13      | 1.3.1         |

## gzip (OK or error reason by Python)
| python/zlib       | 3.8.20 / 1.2.13 | 3.9.23 / 1.2.13 | 3.10.18 / 1.2.13 | 3.11.13 / 1.2.13 | 3.12.11 / 1.2.13 | 3.13.6 / 1.2.8 | 3.13.6 / 1.2.11 | 3.13.6 / 1.2.13 | 3.13.6 / 1.3.1 |
| ----------------- | --------------- | --------------- | ---------------- | ---------------- | ---------------- | -------------- | --------------- | --------------- | -------------- |
| truncated(short)  | EOFError        | EOFError        | EOFError         | EOFError         | EOFError         | EOFError       | EOFError        | EOFError        | EOFError       |
| truncated(long)   | EOFError        | EOFError        | EOFError         | EOFError         | EOFError         | EOFError       | EOFError        | EOFError        | EOFError       |
| crc_flip          | BadGzipFile     | BadGzipFile     | BadGzipFile      | BadGzipFile      | BadGzipFile      | BadGzipFile    | BadGzipFile     | BadGzipFile     | BadGzipFile    |
| isize_flip        | BadGzipFile     | BadGzipFile     | BadGzipFile      | BadGzipFile      | BadGzipFile      | BadGzipFile    | BadGzipFile     | BadGzipFile     | BadGzipFile    |
| last_byte_missing | EOFError        | EOFError        | EOFError         | EOFError         | EOFError         | EOFError       | EOFError        | EOFError        | EOFError       |

## zlib (OK or error reason by Python)
| python/zlib       | 3.8.20 / 1.2.13 | 3.9.23 / 1.2.13 | 3.10.18 / 1.2.13 | 3.11.13 / 1.2.13 | 3.12.11 / 1.2.13 | 3.13.6 / 1.2.8 | 3.13.6 / 1.2.11 | 3.13.6 / 1.2.13 | 3.13.6 / 1.3.1 |
| ----------------- | --------------- | --------------- | ---------------- | ---------------- | ---------------- | -------------- | --------------- | --------------- | -------------- |
| truncated(short)  | OK              | OK              | OK               | OK               | OK               | OK             | OK              | OK              | OK             |
| truncated(long)   | OK              | OK              | OK               | OK               | OK               | OK             | OK              | OK              | OK             |
| crc_flip          | -3              | -3              | -3               | -3               | -3               | -3             | -3              | -3              | -3             |
| isize_flip        | -3              | -3              | -3               | -3               | -3               | -3             | -3              | -3              | -3             |
| last_byte_missing | OK              | OK              | OK               | OK               | OK               | OK             | OK              | OK              | OK             |

**Legend (zlib error codes)**
- `-3`: Z_DATA_ERROR (corrupt data / invalid checksum/length)
- `-5`: Z_BUF_ERROR (incomplete stream / needs more input)

## Environment variation (per case; differences only)
- truncated(short): none
- truncated(long): none
- crc_flip: none
- isize_flip: none
- last_byte_missing: none
