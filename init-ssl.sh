#!/bin/bash
# ══════════════════════════════════════════════════════════════
#  init-ssl.sh — First-time SSL certificate setup
#
#  Run this on the server ONCE before starting the full stack.
#  Handles the chicken-and-egg: nginx needs a cert to start,
#  but certbot needs nginx to complete the ACME challenge.
#
#  Usage:
#    chmod +x init-ssl.sh
#    ./init-ssl.sh YOUR_DOMAIN.com admin@youremail.com
# ══════════════════════════════════════════════════════════════

set -e

DOMAIN=${1:?"Usage: ./init-ssl.sh DOMAIN EMAIL"}
EMAIL=${2:?"Usage: ./init-ssl.sh DOMAIN EMAIL"}

echo "═══════════════════════════════════════════════"
echo "  Initialising SSL for: $DOMAIN"
echo "  Contact email:        $EMAIL"
echo "═══════════════════════════════════════════════"

# ── Step 1: Substitute domain placeholder ─────────────────────
echo "[1/4] Configuring nginx for $DOMAIN..."
sed -i.bak "s/YOUR_DOMAIN.com/$DOMAIN/g" nginx/conf.d/wavemap.conf
echo "       Done — nginx/conf.d/wavemap.conf updated."

# ── Step 2: Start nginx with HTTP-only config (no SSL yet) ─────
echo "[2/4] Starting nginx with bootstrap config..."
cat > nginx/conf.d/bootstrap.conf << EOF
server {
    listen 80;
    server_name $DOMAIN www.$DOMAIN;

    # Required for ACME challenge
    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }

    location / {
        return 200 'WAVEMAP — SSL initialising...';
        add_header Content-Type text/plain;
    }
}
EOF

# Temporarily comment out the ssl_certificate lines so nginx doesn't fail
sed -i.bak 's/ssl_certificate/# ssl_certificate/' nginx/conf.d/wavemap.conf
sed -i.bak 's/listen 443 ssl/listen 443/' nginx/conf.d/wavemap.conf

docker compose up -d nginx
sleep 3
echo "       Nginx started."

# ── Step 3: Issue certificate ──────────────────────────────────
echo "[3/4] Requesting Let's Encrypt certificate..."
docker compose run --rm certbot certonly \
    --webroot \
    --webroot-path=/var/www/certbot \
    --email "$EMAIL" \
    --agree-tos \
    --no-eff-email \
    --force-renewal \
    -d "$DOMAIN" \
    -d "www.$DOMAIN"

echo "       Certificate issued!"

# ── Step 4: Restore full nginx config with SSL ────────────────
echo "[4/4] Enabling HTTPS config..."
# Restore from backup
mv nginx/conf.d/wavemap.conf.bak nginx/conf.d/wavemap.conf
rm nginx/conf.d/bootstrap.conf

# Restart nginx with full SSL config
docker compose restart nginx
sleep 3

echo ""
echo "═══════════════════════════════════════════════"
echo "  SSL certificate installed!"
echo "  Now run: docker compose up -d --build"
echo "  Your site: https://$DOMAIN"
echo "═══════════════════════════════════════════════"
