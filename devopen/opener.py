    if registered:
        # ssh/mosh prep: stable host keys (restore/backup) + sshd, then show
        # the exact mosh command with the container's tailnet IP, and a
        # blink://host deep link using the MagicDNS name (durable across
        # container recreations, unlike the raw IP). If no Blink key is
        # configured, the key param is omitted so Blink uses its default key.
        _ensure_ssh_hostkeys_and_sshd(container_id, on_log=on_log)
        ip = _tailnet_ip(container_id)
        if ip:
            user = _remote_user(folder)
            on_log(f"[devopen] mosh-ready: mosh {user}@{ip}   (ssh {user}@{ip})")
        dns = _tailnet_dnsname(container_id)
        if dns:
            user = _remote_user(folder)
            on_log(f"[devopen] blink-host: {blink_url(dns, user, key=blink_key or None)}")
    uri = open_in_vscode(container_id, folder, on_log=on_log)
    on_log("Done.")
    return uri
