# Compatibility builds only: callback-safe protected-identity query. The split
# registry already has a spinlock; add the same synchronization for host peers.
function(lism_input_patch_identity source destination)
  file(READ "${source}" code)
  set(anchor "void set_profile_address(uint8_t index, const bt_addr_le_t *addr) {")
  lism_smp_replace_once(code "${anchor}"
    "static struct k_spinlock lism_profile_identity_lock;\n\n${anchor}")
  lism_smp_replace_once(code "    memcpy(&profiles[index].peer, addr, sizeof(bt_addr_le_t));"
    "    k_spinlock_key_t identity_key = k_spin_lock(&lism_profile_identity_lock);\n    memcpy(&profiles[index].peer, addr, sizeof(bt_addr_le_t));\n    k_spin_unlock(&lism_profile_identity_lock, identity_key);")
  set(old [=[        int err = read_cb(cb_arg, &profiles[idx], sizeof(struct zmk_ble_profile));
        if (err <= 0) {
            LOG_ERR("Failed to handle profile address from settings (err %d)", err);
            return err;
        }]=])
  set(new [=[        struct zmk_ble_profile loaded_profile;
        int err = read_cb(cb_arg, &loaded_profile, sizeof(loaded_profile));
        if (err != sizeof(loaded_profile)) {
            LOG_ERR("Failed to handle profile address from settings (err %d)", err);
            return err < 0 ? err : -EIO;
        }
        k_spinlock_key_t identity_key = k_spin_lock(&lism_profile_identity_lock);
        profiles[idx] = loaded_profile;
        k_spin_unlock(&lism_profile_identity_lock, identity_key);]=])
  lism_smp_replace_once(code "${old}" "${new}")
  set(old [=[            int err = read_cb(cb_arg, &peripheral_addrs[i], sizeof(bt_addr_le_t));
            if (err <= 0) {
                LOG_ERR("Failed to handle peripheral address from settings (err %d)", err);
                return err;
            }]=])
  set(new [=[            bt_addr_le_t loaded_peripheral;
            int err = read_cb(cb_arg, &loaded_peripheral, sizeof(loaded_peripheral));
            if (err != sizeof(loaded_peripheral)) {
                LOG_ERR("Failed to handle peripheral address from settings (err %d)", err);
                return err < 0 ? err : -EIO;
            }
            k_spinlock_key_t identity_key = k_spin_lock(&lism_split_address_lock);
            bt_addr_le_copy(&peripheral_addrs[i], &loaded_peripheral);
            k_spin_unlock(&lism_split_address_lock, identity_key);]=])
  lism_smp_replace_once(code "${old}" "${new}")
  # No settings calls or mutexes in this query. Do not hold both locks at once.
  string(APPEND code [=[
bool lism_input_protected_identity(const bt_addr_le_t *addr) {
    if (!addr || !bt_addr_le_cmp(addr, BT_ADDR_LE_ANY)) return true;
    if (lism_split_sets_find_addr(addr) >= 0) return true;
    bool protected = false;
    k_spinlock_key_t key = k_spin_lock(&lism_profile_identity_lock);
    for (unsigned i = 0; i < ZMK_BLE_PROFILE_COUNT; i++) {
        if (!bt_addr_le_cmp(addr, &profiles[i].peer)) { protected = true; break; }
    }
    k_spin_unlock(&lism_profile_identity_lock, key);
    return protected;
}
bool lism_input_computer_identity(const bt_addr_le_t *addr) {
    if (!addr || !bt_addr_le_cmp(addr,BT_ADDR_LE_ANY)) return true;
    bool found=false;
    k_spinlock_key_t key=k_spin_lock(&lism_profile_identity_lock);
    for(unsigned i=0;i<ZMK_BLE_PROFILE_COUNT;i++)
        if(!bt_addr_le_cmp(addr,&profiles[i].peer)) {found=true;break;}
    k_spin_unlock(&lism_profile_identity_lock,key);return found;
}
]=])
  file(WRITE "${destination}" "${code}")
endfunction()
