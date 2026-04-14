#!/bin/bash
# ══════════════════════════════════════════════════════════════
#  WAVEMAP — Production Deploy Script
#  Run once on a fresh VPS.
#
#  Usage:
#    chmod +x deploy.sh && ./deploy.sh YOUR_DOMAIN.com your@email.com
# ══════════════════════════════════════════════════════════════

set -euo pipefail

DOMAIN=${1:?"Usage: ./deploy.sh DOMAIN EMAIL"}
EMAIL=${2:?"Usage: ./deploy.sh DOMAIN EMAIL"}

BOLD='\033[1m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'

echo -e "${BOLD}WAVEMAP Deploy — domain: ${GREEN}$DOMAIN${NC}\n"

# ── 1. Substitute domain placeholder in nginx config ──────────
echo -e "${YELLOW}[1/6] Configuring nginx for $DOMAIN...${NC}"
sed -i "s/YOUR_DOMAIN\.com/$DOMAIN/g" nginx/conf.d/wavemap.conf
echo "       Done."

# ── 2. Create / verify .env ───────────────────────────────────
echo -e "${YELLOW}[2/6] Checking .env...${NC}"
if [ ! -f .env ]; then
    cp .env.example .env
    echo -e "\n${RED}${BOLD}⚠️  .env created. Fill in credentials before continuing:${NC}"
    echo "     POSTGRES_PASSWORD, SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET"
    echo "     SPOTIFY_REDIRECT_URI=https://$DOMAIN/auth/callback"
    echo -e "\n     Then re-run: ./deploy.sh $DOMAIN $EMAIL\n"
    exit 1
fi

# shellcheck source=.env
source .env
if [ -z "${SPOTIFY_CLIENT_ID:-}" ] || [ "${SPOTIFY_CLIENT_ID}" = "your_client_id_here" ]; then
    echo -e "${RED}ERROR: SPOTIFY_CLIENT_ID not set in .env${NC}"; exit 1
fi
echo "       .env looks good."

# ── 3. SSL certificate (HTTP-01 challenge) ────────────────────
echo -e "${YELLOW}[3/6] Issuing Let's Encrypt certificate...${NC}"

# Nginx can't start with wavemap.conf because the cert doesn't exist yet.
# Solution: temporarily move it aside and put a plain HTTP-only config in place.
NGINX_CONF="nginx/conf.d/wavemap.conf"
NGINX_CONF_BAK="nginx/conf.d/wavemap.conf.bak"

cp "$NGINX_CONF" "$NGINX_CONF_BAK"

cat > "$NGINX_CONF" <<EOF
# Temporary bootstrap config — HTTP-01 ACME challenge only
server {
    listen 80;
    server_name $DOMAIN www.$DOMAIN;
    location /.well-known/acme-challenge/ { root /var/www/certbot; }
    location / { return 200 'WAVEMAP — setting up SSL...'; add_header Content-Type text/plain; }
}
EOF

docker compose up -d nginx
sleep 3

docker compose run --rm certbot certonly \
    --webroot --webroot-path=/var/www/certbot \
    --email "$EMAIL" --agree-tos --no-eff-email \
    -d "$DOMAIN" -d "www.$DOMAIN"

# Restore full nginx config with SSL
cp "$NGINX_CONF_BAK" "$NGINX_CONF"
rm "$NGINX_CONF_BAK"

docker compose restart nginx
sleep 3
echo -e "${GREEN}       Certificate installed, HTTPS enabled.${NC}"

# ── 4. Build and start full stack ─────────────────────────────
echo -e "${YELLOW}[4/6] Building and starting all services...${NC}"
docker compose up -d --build
echo "       Done."

# ── 5. Health check ───────────────────────────────────────────
echo -e "${YELLOW}[5/6] Waiting for app to become healthy (up to 90 s)...${NC}"
sleep 15
HEALTHY=false
for i in $(seq 1 15); do
    if docker compose exec -T app \
        python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/stats')" \
        2>/dev/null; then
        HEALTHY=true
        break
    fi
    echo "  Waiting... (${i}/15)"
    sleep 5
done

if [ "$HEALTHY" = false ]; then
    echo -e "${RED}  App not healthy after 90 s. Check: docker compose logs app${NC}"
else
    echo -e "${GREEN}  App is healthy!${NC}"
fi

# ── 6. Done ───────────────────────────────────────────────────
echo -e "\n${GREEN}${BOLD}══════════════════════════════════════════════"
echo -e "  WAVEMAP live at  https://$DOMAIN"
echo -e "  API docs:        https://$DOMAIN/docs"
echo -e "══════════════════════════════════════════════${NC}\n"
echo "Useful commands:"
echo "  docker compose logs -f app        # app logs"
echo "  docker compose logs -f nginx      # nginx logs"
echo "  docker compose restart app        # restart app"
echo "  docker compose up -d --build      # redeploy after code changes"
echo ""
echo "To bulk-import tracks from CSV into PostgreSQL:"
echo "  docker compose exec app python backend/load_to_db.py"
echo ""
