#!/bin/bash
# ================================================
# 🚀 Auto LXC Installer for VPS
# 🧑‍💻 Made by DUDE_X_FREEZE
# ================================================

clear
echo "===================================="
echo "     🚀 Installing LXC on VPS..."
echo "===================================="
sleep 2

# Update system
apt update && apt upgrade -y

# Install dependencies
apt install -y lxc lxc-templates bridge-utils debootstrap wget curl net-tools gnupg

# Check virtualization
virt=$(systemd-detect-virt)
echo "🔍 Virtualization Type: $virt"

if [[ "$virt" == "openvz" || "$virt" == "lxc" ]]; then
    echo "❌ Nested LXC not supported in this virtualization ($virt)."
    echo "Please use KVM-based VPS."
    exit 1
fi

# Enable cgroups if missing
echo "✅ Ensuring cgroups support..."
modprobe br_netfilter
modprobe overlay
echo "br_netfilter" >> /etc/modules-load.d/lxc.conf
echo "overlay" >> /etc/modules-load.d/lxc.conf

# Check kernel config
lxc-checkconfig

# Create default bridge
if ! ip link show lxcbr0 &> /dev/null; then
    echo "🌐 Creating default LXC bridge (lxcbr0)..."
    cat <<EOF > /etc/network/interfaces.d/lxcbr0.cfg
auto lxcbr0
iface lxcbr0 inet static
  bridge_ports none
  bridge_fd 0
  bridge_maxwait 0
  address 10.0.3.1
  netmask 255.255.255.0
EOF
    systemctl restart networking
fi

# Create test container
echo "📦 Creating test LXC container (ubuntu-test)..."
lxc-create -n ubuntu-test -t ubuntu

# Start container
echo "🚀 Starting container..."
lxc-start -n ubuntu-test -d

sleep 5

# Check container status
echo "📋 Container List:"
lxc-ls --fancy

# Show access instructions
echo ""
echo "===================================="
echo "✅ LXC Installation Completed!"
echo "🧩 Test Container: ubuntu-test"
echo "🖥️ To attach: sudo lxc-attach -n ubuntu-test"
echo "🛑 To stop:   sudo lxc-stop -n ubuntu-test"
echo "💣 To delete: sudo lxc-destroy -n ubuntu-test"
echo "===================================="
