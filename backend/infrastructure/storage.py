"""
infrastructure/storage.py  (담당: 팀원 B / 공통)

S3 객체 저장 공통 헬퍼. 파일 업로드 / 다운로드(presigned URL) / 삭제.
- 버킷·리전은 data_gateway 와 동일(kira-documents..., 서울). 비공개 버킷.
- 자격증명은 EC2 IAM Role 자동 주입(키 미전달). 로컬에선 호출 시점에 실패할 수 있음 —
  실제 동작 검증은 EC2 배포 시점.
- 동기 boto3 호출은 asyncio.to_thread 로 감싸 이벤트 루프를 막지 않는다.
- file_url/s3_key 컬럼엔 영구 URL이 아니라 버킷 내 '키'를 저장한다.
"""
import asyncio

import boto3

# data_gateway 와 동일 버킷/리전 재사용. [BYPASS:C4]
S3_BUCKET = "kira-documents-423937245947-ap-northeast-2-an"
AWS_REGION = "ap-northeast-2"

# boto3 client는 스레드 안전 — 모듈 레벨 1회 생성. 자격증명은 IAM Role.
_s3_client = boto3.client("s3", region_name=AWS_REGION)


async def upload_bytes(key: str, data: bytes, content_type: str | None = None) -> None:
    """바이트를 S3 키로 업로드(put_object). content_type 지정 시 메타로 저장."""
    def _put() -> None:
        extra = {"ContentType": content_type} if content_type else {}
        _s3_client.put_object(Bucket=S3_BUCKET, Key=key, Body=data, **extra)
    await asyncio.to_thread(_put)


async def generate_presigned_url(key: str, expires_in: int = 3600) -> str:
    """키에 대한 임시 다운로드 URL(get_object presigned, 기본 1시간)."""
    def _gen() -> str:
        return _s3_client.generate_presigned_url(
            "get_object",
            Params={"Bucket": S3_BUCKET, "Key": key},
            ExpiresIn=expires_in,
        )
    return await asyncio.to_thread(_gen)


async def delete_object(key: str) -> None:
    """S3 객체 삭제."""
    def _del() -> None:
        _s3_client.delete_object(Bucket=S3_BUCKET, Key=key)
    await asyncio.to_thread(_del)
