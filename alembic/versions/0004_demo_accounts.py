"""0004 — 데모 로그인 계정 시드 (data migration)

기본 시연용 로그인 계정 2개를 넣는다. seed(02_seed_data.sql)는 빈 볼륨 최초 init에만
실행되므로, 볼륨을 보존하는 기존/배포 DB에는 반영되지 않는다. 부팅 시 자동 적용되는
alembic 경로로 넣어야 EC2 기존 DB에도 들어간다(데이터 보존).

  원청사  oem@kira-dpp.com              / demo1234  (role admin)
  협력사  supplier@sulawesi-nickel.com  / demo1234  (role supplier_ceo)

- 둘 다 시드 테넌트(a0eebc99…) 소속 → 테넌트 격리(§0.2) 라우트 접근 가능.
- role은 users CHECK 제약(admin/owner_*/supplier_*)에 맞춤. (프론트 'oem'/'supplier'와 다르면 로그인 응답에서 매핑)
- password_hash 는 컨테이너 bcrypt 로 생성한 'demo1234' 해시.

[멱등성] ON CONFLICT(email) DO UPDATE — 재적용 안전.
[안전] 대상 테넌트가 없으면 WHERE EXISTS 로 스킵 → FK 실패로 부팅 깨지지 않음.

Revision ID: 0004_demo_accounts
Revises: 0004_due_diligence_columns

[머지 선형화] 원래 0003에서 갈라졌으나, 같은 0003에서 분기한 0004_due_diligence_columns
(D delta)와 head가 충돌(multiple heads)하여 부팅이 깨졌다. 두 0004를 직렬로 잇기 위해
down_revision 을 0004_due_diligence_columns 로 re-point 한다(체인 단일화, 데이터 보존).
"""
from alembic import op  # noqa: F401

revision = "0004_demo_accounts"
down_revision = "0004_due_diligence_columns"
branch_labels = None
depends_on = None

_TENANT = "a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11"
_HASH = "$2b$12$LdrfIceVZR7twTzU8rxKF.M0uqv9vmcUawZNKRoLjbjb9gAidiynS"


def upgrade() -> None:
    op.execute(
        f"""
        INSERT INTO users (user_id, tenant_id, email, password_hash, name, role)
        SELECT v.user_id, v.tenant_id, v.email, v.password_hash, v.name, v.role
        FROM (VALUES
          ('11111111-0000-4000-8000-0000000000a1'::uuid, '{_TENANT}'::uuid,
           'oem@kira-dpp.com', '{_HASH}', 'Demo OEM', 'admin'),
          ('11111111-0000-4000-8000-0000000000b1'::uuid, '{_TENANT}'::uuid,
           'supplier@sulawesi-nickel.com', '{_HASH}', 'Demo Supplier', 'supplier_ceo')
        ) AS v(user_id, tenant_id, email, password_hash, name, role)
        WHERE EXISTS (SELECT 1 FROM tenants t WHERE t.tenant_id = v.tenant_id)
        ON CONFLICT (email) DO UPDATE
          SET password_hash = EXCLUDED.password_hash, role = EXCLUDED.role;
        """
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM users WHERE email IN "
        "('oem@kira-dpp.com', 'supplier@sulawesi-nickel.com');"
    )
