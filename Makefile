.PHONY: test cov serve worker openapi sdk check

test:
	python -m pytest --cov=src --cov-fail-under=85

sdk-test:
	npm test --prefix sdk/typescript

serve:
	uvicorn pik_api.main:create_app --factory --host 127.0.0.1 --port 8400

worker:
	python -m pik_api.worker

openapi:
	python scripts/export_openapi.py

sdk:
	sh scripts/generate_sdks.sh

measure:
	python scripts/measure.py
