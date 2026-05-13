# Production Deployment Guide

This guide covers deploying the Portfolio Manager application to production environments.

## Prerequisites

- Python 3.10+
- SQL Server 2016+ (ODBC Driver 18 for SQL Server installed)
- Redis (for production caching)
- Nginx or Apache web server
- HTTPS certificate (Let's Encrypt recommended)
- Linux server (Ubuntu 20.04+ recommended)

## Step 1: Environment Setup

### 1.1 Create .env File

```bash
# Copy template
cp .env.example .env

# Edit with production values
nano .env
```

**Critical Variables to Configure**:

```bash
# Security - MUST CHANGE
DJANGO_SECRET_KEY=your-generated-secret-key-here
DB_USER=your_database_user
DB_PASSWORD=your_secure_password
ALLOWED_HOSTS=example.com,www.example.com

# Deployment
DEBUG=false
SECURE_SSL_REDIRECT=true
SESSION_COOKIE_SECURE=true
CSRF_COOKIE_SECURE=true
SECURE_HSTS_SECONDS=31536000

# Cache (use Redis in production)
CACHE_BACKEND=django.core.cache.backends.redis.RedisCache
CACHE_LOCATION=redis://localhost:6379/1
```

### 1.2 File Permissions

```bash
# Restrict .env file access
chmod 600 .env

# Create and protect logs directory
mkdir -p logs
chmod 755 logs
```

## Step 2: Application Setup

### 2.1 Install Dependencies

```bash
# Create virtual environment
python3.10 -m venv venv
source venv/bin/activate

# Upgrade pip
pip install --upgrade pip

# Install dependencies
pip install -r requirements.txt

# Install production server
pip install gunicorn redis
```

### 2.2 Database Setup

**SQL Server Configuration**:

```sql
-- Create application user (don't use 'sa')
CREATE USER portfolio_user WITH PASSWORD 'StrongPassword123!@#';

-- Grant necessary permissions
USE [PortfolioDev];
GO

GRANT CONNECT ON DATABASE [PortfolioDev] TO portfolio_user;
GRANT SELECT, INSERT, UPDATE, DELETE ON SCHEMA::dbo TO portfolio_user;

-- Create required indexes for performance
CREATE INDEX idx_transaction_date ON Transaction(date);
CREATE INDEX idx_transaction_symbol ON Transaction(symbol);
CREATE INDEX idx_account_owner ON Account(account_id);
```

### 2.3 Django Database Migrations

```bash
# Run migrations
python manage.py migrate

# Create superuser for admin access
python manage.py createsuperuser

# Collect static files
python manage.py collectstatic --noinput
```

## Step 3: Web Server Configuration

### 3.1 Gunicorn Setup

Create `/etc/systemd/system/portfolio-manager.service`:

```ini
[Unit]
Description=Portfolio Manager Django Application
After=network.target

[Service]
User=www-data
Group=www-data
WorkingDirectory=/var/www/portfolio_manager
Environment="PATH=/var/www/portfolio_manager/venv/bin"
ExecStart=/var/www/portfolio_manager/venv/bin/gunicorn \
    --workers 4 \
    --worker-class sync \
    --bind 127.0.0.1:8000 \
    --timeout 60 \
    --access-logfile /var/log/portfolio_manager/access.log \
    --error-logfile /var/log/portfolio_manager/error.log \
    portfolio_manager.wsgi:application

Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable portfolio-manager
sudo systemctl start portfolio-manager
sudo systemctl status portfolio-manager
```

### 3.2 Nginx Configuration

Create `/etc/nginx/sites-available/portfolio-manager`:

```nginx
# Redirect HTTP to HTTPS
server {
    listen 80;
    listen [::]:80;
    server_name example.com www.example.com;
    
    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }
    
    location / {
        return 301 https://$server_name$request_uri;
    }
}

# HTTPS configuration
server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name example.com www.example.com;
    
    # SSL Certificates (Let's Encrypt)
    ssl_certificate /etc/letsencrypt/live/example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/example.com/privkey.pem;
    
    # SSL Configuration
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 10m;
    
    # Security Headers
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains; preload" always;
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    
    # Gzip compression
    gzip on;
    gzip_types text/plain text/css application/json application/javascript;
    gzip_min_length 1000;
    
    # Static files with long cache
    location /static/ {
        alias /var/www/portfolio_manager/static/;
        expires 30d;
        add_header Cache-Control "public, immutable";
    }
    
    # Media files
    location /media/ {
        alias /var/www/portfolio_manager/media/;
        expires 7d;
    }
    
    # Application proxy
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host $server_name;
        proxy_redirect off;
        
        # Timeouts
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
    }
    
    # Deny access to sensitive files
    location ~ /\. {
        deny all;
    }
    
    location ~ ~$ {
        deny all;
    }
}
```

Enable the site:

```bash
sudo ln -s /etc/nginx/sites-available/portfolio-manager /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

### 3.3 SSL Certificate (Let's Encrypt)

```bash
# Install certbot
sudo apt-get install certbot python3-certbot-nginx

# Get certificate
sudo certbot certonly --webroot -w /var/www/certbot -d example.com -d www.example.com

# Auto-renewal
sudo systemctl enable certbot.timer
sudo systemctl start certbot.timer
```

## Step 4: Database Security

### 4.1 SQL Server Configuration

**Enable Encryption**:

```sql
-- Enable SSL/TLS for SQL Server (configuration in SQL Server Configuration Manager)
-- Restart SQL Server service after configuration
```

**Firewall Rules**:

```bash
# Only allow connection from application server
sudo ufw allow from 10.0.1.100 to any port 1433
sudo ufw deny from any to any port 1433
```

**Backup Configuration**:

```sql
-- Daily backup job
BACKUP DATABASE [PortfolioDev] 
TO DISK = N'/backups/PortfolioDev_$(DATE).bak'
WITH COMPRESSION, ENCRYPTION (
    ALGORITHM = AES_256,
    SERVER CERTIFICATE = MasterKey
);
```

## Step 5: Monitoring & Logging

### 5.1 Application Logs

```bash
# View application logs
sudo journalctl -u portfolio-manager -n 50 -f

# View Nginx logs
sudo tail -f /var/log/nginx/portfolio_manager_error.log
sudo tail -f /var/log/nginx/portfolio_manager_access.log
```

### 5.2 Log Rotation

Create `/etc/logrotate.d/portfolio-manager`:

```
/var/log/portfolio_manager/*.log {
    daily
    rotate 14
    compress
    delaycompress
    notifempty
    create 0640 www-data www-data
    sharedscripts
    postrotate
        systemctl reload portfolio-manager > /dev/null 2>&1 || true
    endscript
}
```

### 5.3 Health Check Endpoint

```bash
# Check application is running
curl -I https://example.com/login/

# Monitor response times
ab -n 100 -c 10 https://example.com/
```

## Step 6: Backup & Recovery

### 6.1 Automated Backups

Create `/usr/local/bin/backup-portfolio.sh`:

```bash
#!/bin/bash
set -e

BACKUP_DIR="/backups/portfolio_manager"
DATE=$(date +%Y%m%d_%H%M%S)
DB_NAME="PortfolioDev"

# Create backup directory
mkdir -p $BACKUP_DIR

# SQL Server backup
sqlcmd -S localhost -U portfolio_user -P $DB_PASSWORD \
    -Q "BACKUP DATABASE [$DB_NAME] TO DISK = N'$BACKUP_DIR/db_$DATE.bak' WITH COMPRESSION"

# Application files backup
tar -czf $BACKUP_DIR/app_$DATE.tar.gz \
    --exclude='venv' \
    --exclude='__pycache__' \
    --exclude='.git' \
    /var/www/portfolio_manager

# Keep only last 30 days of backups
find $BACKUP_DIR -mtime +30 -delete

echo "Backup completed: $DATE"
```

Add to crontab:

```bash
# Run daily at 2 AM
0 2 * * * /usr/local/bin/backup-portfolio.sh >> /var/log/portfolio_backup.log 2>&1
```

### 6.2 Recovery Procedure

```bash
# Restore database
sqlcmd -S localhost -U portfolio_user -P $DB_PASSWORD \
    -Q "RESTORE DATABASE [PortfolioDev] FROM DISK = N'/backups/portfolio_manager/db_YYYYMMDD_HHMMSS.bak'"

# Restore application files
cd /var/www
tar -xzf /backups/portfolio_manager/app_YYYYMMDD_HHMMSS.tar.gz

# Restart application
sudo systemctl restart portfolio-manager
```

## Step 7: Security Hardening

### 7.1 OS Level

```bash
# Update all packages
sudo apt-get update
sudo apt-get upgrade -y

# Install and configure firewall
sudo apt-get install ufw
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw allow from 10.0.1.100 to any port 1433
sudo ufw enable

# Disable SSH password auth
sudo sed -i 's/^#PasswordAuthentication yes/PasswordAuthentication no/' /etc/ssh/sshd_config
sudo systemctl restart ssh

# Fail2Ban for intrusion prevention
sudo apt-get install fail2ban
sudo systemctl enable fail2ban
```

### 7.2 Django Level

```bash
# Run security checks
python manage.py check --deploy

# Expected output: All checks passed
```

### 7.3 Regular Security Updates

```bash
# Monthly dependency updates
python -m pip list --outdated
pip install --upgrade -r requirements.txt

# Test in staging first
python manage.py test

# Deploy to production after testing
```

## Step 8: Performance Tuning

### 8.1 Database Optimization

```sql
-- Check table sizes
SELECT 
    OBJECT_NAME(p.object_id) AS TableName,
    SUM(p.rows) AS RowCount,
    SUM(a.total_pages) * 8 AS TotalSpaceKB
FROM sys.partitions p
JOIN sys.allocation_units a ON p.partition_id = a.container_id
GROUP BY p.object_id
ORDER BY SUM(p.rows) DESC;

-- Create necessary indexes
CREATE INDEX idx_tx_account_date ON Transaction(account_id, date);
CREATE INDEX idx_holdings_symbol ON Holdings(symbol);
```

### 8.2 Application Optimization

```bash
# Enable Redis caching
# Configure in .env: CACHE_BACKEND and CACHE_LOCATION

# Use connection pooling
# Already configured with django-mssql

# Optimize static files
python manage.py collectstatic --noinput

# Consider CDN for static assets
```

### 8.3 Monitoring

```bash
# Install monitoring (optional but recommended)
pip install django-extensions django-debug-toolbar

# Monitor memory usage
watch -n 5 'free -h'

# Monitor database connections
sqlcmd -S localhost -Q "SELECT COUNT(*) FROM sys.dm_exec_sessions"
```

## Step 9: Testing Deployment

### 9.1 Functional Tests

```bash
# Test login
curl -X POST https://example.com/login/ \
    -d "username=testuser&password=testpass"

# Test API endpoints
curl -H "Authorization: Bearer $TOKEN" https://example.com/api/accounts/

# Test data export
curl -H "Authorization: Bearer $TOKEN" https://example.com/transactions/export-csv/
```

### 9.2 Security Tests

```bash
# Check SSL configuration
openssl s_client -connect example.com:443

# Check security headers
curl -I https://example.com/ | grep -i strict

# Check for common vulnerabilities
# Use OWASP ZAP or Burp Suite for comprehensive testing
```

## Troubleshooting

### Application Won't Start

```bash
# Check logs
sudo journalctl -u portfolio-manager -n 50

# Check environment variables
printenv | grep DJANGO

# Check database connection
python manage.py dbshell
```

### Slow Performance

```bash
# Check database queries
python manage.py shell
>>> from django.db import connection
>>> from django.test.utils import override_settings
>>> connection.queries

# Check cache status
redis-cli INFO stats

# Check system resources
top -u www-data
```

### SSL Certificate Issues

```bash
# Check certificate expiration
openssl x509 -enddate -noout -in /etc/letsencrypt/live/example.com/cert.pem

# Renew certificate
sudo certbot renew --dry-run

# Check Nginx SSL config
sudo nginx -T
```

## Maintenance Tasks

### Weekly
- Monitor application logs
- Check system resources
- Verify backup completion

### Monthly
- Update dependencies
- Review security logs
- Test disaster recovery procedure
- Check SSL certificate expiration

### Quarterly
- Full security audit
- Database optimization review
- Performance analysis
- Disaster recovery drill

---

**Last Updated**: May 13, 2026  
**Maintained by**: DevOps Team
