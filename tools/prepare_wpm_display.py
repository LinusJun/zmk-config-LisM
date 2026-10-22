"""Keep WPM animation on the LVGL display queue and clear an idle peak."""
from prepare_upstream_esb import replace

def prepare(path):
    s=path.read_text()
    s=replace(s,'k_work_schedule(&wpm_smooth_work, K_MSEC(33));',
        'k_work_schedule_for_queue(zmk_display_work_q(), &wpm_smooth_work, K_MSEC(33));')
    s=replace(s,'k_work_schedule(&wpm_smooth_work, K_NO_WAIT);',
        'k_work_schedule_for_queue(zmk_display_work_q(), &wpm_smooth_work, K_NO_WAIT);')
    s=replace(s,'    bool peak_changed = false;',
        '    /* Once the displayed value reaches zero, leave no delayed peak. */\n'
        '    if (target_wpm == 0.0f && new_int == 0) {\n'
        '        bool dirty = old_int != 0 || prev_active_bars != 0 || peak_position != 0;\n'
        '        displayed_wpm = 0.0f;\n'
        '        peak_position = peak_hold_counter = peak_decay_counter = 0;\n'
        '        if (dirty) wpm_meter_render(0);\n'
        '        return;\n'
        '    }\n\n'
        '    bool peak_changed = false;')
    # XIAO resizes the bars natively; center its peak on the actual bar.
    s=replace(s,'            lv_obj_set_pos(widget->peak_indicator, peak_x, 0);',
        '#if defined(CONFIG_LISM_XIAO147_TOUCH)\n'
        '            peak_x = lv_obj_get_style_x(widget->bars[peak_slot], 0) +\n'
        '                (lv_obj_get_style_width(widget->bars[peak_slot], 0) - 4) / 2;\n'
        '#endif\n'
        '            lv_obj_set_pos(widget->peak_indicator, peak_x, 0);')
    init='    k_work_init_delayable(&wpm_smooth_work, wpm_smooth_work_handler);'
    s=replace(s,init,'')
    s=replace(s,'    widget_wpm_meter_init();',
        '    /* The listener init immediately schedules the first animation. */\n'+init+'\n'
        '    widget_wpm_meter_init();')
    assert 'k_work_schedule(&wpm_smooth_work' not in s
    path.write_text(s)
