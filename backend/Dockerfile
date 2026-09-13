FROM python:3.11-slim

# liboqs-python (the PyPI package) is a Python wrapper ONLY — it does not
# ship a compiled liboqs. On first `import oqs`, it downloads and builds the
# native liboqs C library from source, which takes several minutes. We need
# a compiler toolchain, cmake, ninja and OpenSSL headers to do that build.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    ninja-build \
    git \
    libssl-dev \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Trigger the liboqs native build NOW, at image-build time, rather than
# letting it happen lazily on the first pipeline run. Without this, the
# Tester agent's 15s sandboxed subprocess would try to build liboqs from
# source on its first `import oqs` and almost certainly time out. This also
# exercises ML-KEM-512 once so a broken build fails loudly here, not later.
RUN python3 -c "\
import oqs; \
kem = oqs.KeyEncapsulation('ML-KEM-512'); \
pk = kem.generate_keypair(); \
ct, ss = kem.encap_secret(pk); \
print('liboqs build OK — ML-KEM-512 keypair/encap verified')"

COPY . .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
