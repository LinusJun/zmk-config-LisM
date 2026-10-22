/*
 * Copyright (c) 2017 Jan Van Winkel <jan.van_winkel@dxplore.eu>
 * Copyright (c) 2019 Nordic Semiconductor ASA
 * Copyright (c) 2019 Marc Reilly
 * Copyright (c) 2019 PHYTEC Messtechnik GmbH
 * Copyright (c) 2020 Endian Technologies AB
 * Copyright (c) 2022 Basalte bv
 *
 * SPDX-License-Identifier: Apache-2.0
 */

#define DT_DRV_COMPAT sitronix_st7789v

/* XIAO-only fork of Prospector ed98221f3b52b7066dbb10ba3af8a29150b93a5a.
 * JD9853A sequence follows Seeed_GFX2 c173f5c37d67dc38765a188a6754899fc05663e9.
 * The original Prospector dongle continues to use the unmodified upstream driver.
 */
#include "display_st7789v.h"

#include <zephyr/device.h>
#include <zephyr/drivers/mipi_dbi.h>
#include <zephyr/pm/device.h>
#include <zephyr/sys/byteorder.h>
#include <zephyr/drivers/display.h>

#define LOG_LEVEL CONFIG_DISPLAY_LOG_LEVEL
#include <zephyr/logging/log.h>
LOG_MODULE_REGISTER(xiao147_panel);
BUILD_ASSERT(IS_ENABLED(CONFIG_ST7789V_RGB565), "XIAO panel requires native RGB565");

struct st7789v_config {
	const struct device *mipi_dbi;
	const struct mipi_dbi_config dbi_config;
	uint8_t vcom;
	uint8_t gctrl;
	bool vdv_vrh_enable;
	uint8_t vrh_value;
	uint8_t vdv_value;
	uint8_t mdac;
	uint8_t gamma;
	uint8_t colmod;
	uint8_t lcm;
	bool inversion_on;
	uint8_t porch_param[5];
	uint8_t cmd2en_param[4];
	uint8_t pwctrl1_param[2];
	uint8_t pvgam_param[14];
	uint8_t nvgam_param[14];
	uint8_t ram_param[2];
	uint8_t rgb_param[3];
	uint16_t height;
	uint16_t width;
	uint8_t ready_time_ms;
};

struct st7789v_data {
	uint16_t x_offset;
	uint16_t y_offset;
	enum display_orientation orientation;
};

#ifdef CONFIG_ST7789V_RGB888
#define ST7789V_PIXEL_SIZE 3u
#else
#define ST7789V_PIXEL_SIZE 2u
#endif

static void st7789v_set_lcd_margins(const struct device *dev,
				    uint16_t x_offset, uint16_t y_offset)
{
	struct st7789v_data *data = dev->data;

	data->x_offset = x_offset;
	data->y_offset = y_offset;
}

static int st7789v_transmit(const struct device *dev, uint8_t cmd,
			    uint8_t *tx_data, size_t tx_count)
{
	const struct st7789v_config *config = dev->config;

	return mipi_dbi_command_write(config->mipi_dbi, &config->dbi_config,
				      cmd, tx_data, tx_count);
}

static int st7789v_exit_sleep(const struct device *dev)
{
	int ret;

	ret = st7789v_transmit(dev, ST7789V_CMD_SLEEP_OUT, NULL, 0);
	if (ret < 0) {
		return ret;
	}

	k_sleep(K_MSEC(120));
	return ret;
}

static int st7789v_reset_display(const struct device *dev)
{
	const struct st7789v_config *config = dev->config;
	int ret;

	LOG_DBG("Resetting display");

	k_sleep(K_MSEC(1));
	/* LCD and CTP reset are tied by board R9. Seeed's AXS reset holds low
	 * for 200 ms; do it once here, not concurrently in the touch driver.
	 */
	ret = mipi_dbi_reset(config->mipi_dbi, 200);
	if (ret == -ENOTSUP) {
		/* Send software reset command */
		ret = st7789v_transmit(dev, ST7789V_CMD_SW_RESET, NULL, 0);
		if (ret < 0) {
			return ret;
		}
		k_sleep(K_MSEC(150));
	} else {
		k_sleep(K_MSEC(150));
	}

	return ret;
}

static int st7789v_blanking_on(const struct device *dev)
{
	return st7789v_transmit(dev, ST7789V_CMD_DISP_OFF, NULL, 0);
}

static int st7789v_blanking_off(const struct device *dev)
{
	return st7789v_transmit(dev, ST7789V_CMD_DISP_ON, NULL, 0);
}

static int st7789v_set_mem_area(const struct device *dev, const uint16_t x,
				 const uint16_t y, const uint16_t w, const uint16_t h)
{
	struct st7789v_data *data = dev->data;
	uint16_t spi_data[2];

	uint16_t ram_x = x + data->x_offset;
	uint16_t ram_y = y + data->y_offset;

	int ret;

	spi_data[0] = sys_cpu_to_be16(ram_x);
	spi_data[1] = sys_cpu_to_be16(ram_x + w - 1);
	ret = st7789v_transmit(dev, ST7789V_CMD_CASET, (uint8_t *)&spi_data[0], 4);
	if (ret < 0) {
		return ret;
	}

	spi_data[0] = sys_cpu_to_be16(ram_y);
	spi_data[1] = sys_cpu_to_be16(ram_y + h - 1);
	return st7789v_transmit(dev, ST7789V_CMD_RASET, (uint8_t *)&spi_data[0], 4);
}

/* Exactly one endian conversion, in this driver. LVGL and software rotation
 * supply native-endian RGB565; RAMCTRL 00 E0 consumes MSB-first wire bytes.
 * Never mutate LVGL's buffer (it may be reused on the following frame).
 */
static uint8_t wire_pixels[512];

static int st7789v_write(const struct device *dev, const uint16_t x,
                        const uint16_t y, const struct display_buffer_descriptor *desc,
                        const void *buf)
{
    const struct st7789v_config *config = dev->config;
    const uint16_t *pixels = buf;
    if (!buf || !desc->width || !desc->height || desc->width > desc->pitch ||
        x + desc->width > config->width || y + desc->height > config->height ||
        (size_t)desc->pitch * desc->height * 2 > desc->buf_size) {
        return -EINVAL;
    }
    int ret = st7789v_set_mem_area(dev, x, y, desc->width, desc->height);
    if (ret < 0) { return ret; }
    ret = st7789v_transmit(dev, ST7789V_CMD_RAMWR, NULL, 0);
    if (ret < 0) { return ret; }
    /* The pinned MIPI SPI backend sends desc->buf_size bytes unchanged.
     * RAMWR advances through the whole window, not one row per transaction.
     * Pack across short portrait rows while skipping source pitch padding. */
    size_t total = (size_t)desc->width * desc->height;
    for (size_t cursor = 0; cursor < total; ) {
            uint16_t count = MIN(total - cursor, sizeof(wire_pixels) / 2);
            for (uint16_t i = 0; i < count; i++) {
                size_t logical = cursor + i;
                uint16_t pixel = pixels[(logical / desc->width) * desc->pitch +
                                        logical % desc->width];
                sys_put_be16(pixel, &wire_pixels[i * 2]);
            }
            struct display_buffer_descriptor chunk = {
                .width = count, .height = 1, .pitch = count, .buf_size = count * 2U,
                .frame_incomplete = cursor + count < total ||
                                    desc->frame_incomplete,
            };
            ret = mipi_dbi_write_display(config->mipi_dbi, &config->dbi_config,
                                        wire_pixels, &chunk, PIXEL_FORMAT_RGB_565);
            if (ret < 0) { return ret; }
            cursor += count;
    }
    return 0;
}

static void st7789v_get_capabilities(const struct device *dev,
			      struct display_capabilities *capabilities)
{
	const struct st7789v_config *config = dev->config;
	const struct st7789v_data *data = dev->data;

	memset(capabilities, 0, sizeof(struct display_capabilities));

	/* Swap dimensions for 90° and 270° rotations (MV bit swaps axes) */
	if (data->orientation == DISPLAY_ORIENTATION_ROTATED_90 ||
	    data->orientation == DISPLAY_ORIENTATION_ROTATED_270) {
		capabilities->x_resolution = config->height;
		capabilities->y_resolution = config->width;
	} else {
		capabilities->x_resolution = config->width;
		capabilities->y_resolution = config->height;
	}

#ifdef CONFIG_ST7789V_RGB565
	capabilities->supported_pixel_formats = PIXEL_FORMAT_RGB_565;
	capabilities->current_pixel_format = PIXEL_FORMAT_RGB_565;
#elif CONFIG_ST7789V_BGR565
	capabilities->supported_pixel_formats = PIXEL_FORMAT_BGR_565;
	capabilities->current_pixel_format = PIXEL_FORMAT_BGR_565;
#else
	capabilities->supported_pixel_formats = PIXEL_FORMAT_RGB_888;
	capabilities->current_pixel_format = PIXEL_FORMAT_RGB_888;
#endif
	capabilities->current_orientation = data->orientation;
}

static int st7789v_set_pixel_format(const struct device *dev,
			     const enum display_pixel_format pixel_format)
{
#ifdef CONFIG_ST7789V_RGB565
	if (pixel_format == PIXEL_FORMAT_RGB_565) {
#elif CONFIG_ST7789V_BGR565
	if (pixel_format == PIXEL_FORMAT_BGR_565) {
#else
	if (pixel_format == PIXEL_FORMAT_RGB_888) {
#endif
		return 0;
	}
	LOG_ERR("Pixel format change not implemented");
	return -ENOTSUP;
}

static int st7789v_set_orientation(const struct device *dev,
				   const enum display_orientation orientation)
{
	const struct st7789v_config *config = dev->config;
	struct st7789v_data *data = dev->data;
	int ret;

	/* only modifying the MY, MX, MV bits, keep existing MDAC config */
	uint8_t tx_data = config->mdac & (ST7789V_MADCTL_ML | ST7789V_MADCTL_BGR |
					  ST7789V_MADCTL_MH_RIGHT_TO_LEFT);

	uint16_t x_offset = 0;
	uint16_t y_offset = 0;

	uint16_t row_offset = 0;
	uint16_t col_offset = 0;

	row_offset = data->y_offset;
	col_offset = data->x_offset;

	switch (orientation) {
	case DISPLAY_ORIENTATION_NORMAL:
		tx_data |= ST7789V_MADCTL_MV_NORMAL_MODE;
		x_offset = data->x_offset;
		y_offset = data->y_offset;
		break;

	case DISPLAY_ORIENTATION_ROTATED_90:
		tx_data |= (ST7789V_MADCTL_MY_BOTTOM_TO_TOP | ST7789V_MADCTL_MV_REVERSE_MODE);
		x_offset = row_offset;
		y_offset = col_offset;
		break;

	case DISPLAY_ORIENTATION_ROTATED_180:
		tx_data |= (ST7789V_MADCTL_MY_BOTTOM_TO_TOP | ST7789V_MADCTL_MX_RIGHT_TO_LEFT);
		x_offset = col_offset;
		y_offset = row_offset;
		break;

	case DISPLAY_ORIENTATION_ROTATED_270:
		tx_data |= (ST7789V_MADCTL_MX_RIGHT_TO_LEFT | ST7789V_MADCTL_MV_REVERSE_MODE);
		x_offset = data->y_offset;
		y_offset = data->x_offset;
		break;

	default:
		LOG_ERR("Error changing display orientation");
		return -ENOTSUP;
	}

	st7789v_set_lcd_margins(dev, x_offset, y_offset);
	ret = st7789v_transmit(dev, ST7789V_CMD_MADCTL, &tx_data, 1U);
	if (ret < 0) {
		return ret;
	}
	data->orientation = orientation;
	LOG_INF("Changed orientation to: '%d'", data->orientation);

	return 0;
}

/* Reset does not initialize LCD GRAM. Clear while blanked so settings loading
 * or a delayed first UI frame cannot expose random/stale pixels at startup. */
static int st7789v_clear_gram(const struct device *dev)
{
    const struct st7789v_config *config=dev->config;
    int ret=st7789v_set_mem_area(dev,0,0,config->width,config->height);
    if(ret<0)return ret;
    ret=st7789v_transmit(dev,ST7789V_CMD_RAMWR,NULL,0);
    if(ret<0)return ret;
    memset(wire_pixels,0,sizeof(wire_pixels));
    size_t remaining=(size_t)config->width*config->height;
    while(remaining) {
        uint16_t count=MIN(remaining,sizeof(wire_pixels)/2);
        struct display_buffer_descriptor chunk={.width=count,.height=1,.pitch=count,
            .buf_size=count*2U,.frame_incomplete=remaining>count};
        ret=mipi_dbi_write_display(config->mipi_dbi,&config->dbi_config,
                                  wire_pixels,&chunk,PIXEL_FORMAT_RGB_565);
        if(ret<0)return ret;
        remaining-=count;
    }
    return 0;
}

static int st7789v_lcd_init(const struct device *dev)
{
    const struct st7789v_config *config = dev->config;
    int ret = st7789v_exit_sleep(dev);
    if (ret < 0) { return ret; }
    /* Seeed JD9853A inherits its 172x320 ST7789-compatible sequence.
     * No Prospector-only CMD2EN, DGMEN or RGBCTRL programming here.
     */
    const struct { uint8_t cmd; uint8_t len; uint8_t data[14]; } sequence[] = {
        {0x13, 0, {0}},                       /* NORON */
        {0x36, 1, {config->mdac}},             /* landscape MADCTL 28 */
        {0xb6, 2, {0x0a, 0x82}},
        {0xb0, 2, {0x00, 0xe0}},              /* MSB-first RGB565 */
        {0x3a, 1, {0x55}},
        {0xb2, 5, {0x0c, 0x0c, 0x00, 0x33, 0x33}},
        {0xb7, 1, {0x35}},
        {0xbb, 1, {0x28}},
        {0xc0, 1, {0x0c}},
        {0xc2, 2, {0x01, 0xff}},
        {0xc3, 1, {0x10}},
        {0xc4, 1, {0x20}},
        {0xc6, 1, {0x0f}},
        {0xd0, 2, {0xa4, 0xa1}},
        {0xe0, 14, {0xd0, 0x00, 0x02, 0x07, 0x0a, 0x28, 0x32,
                   0x44, 0x42, 0x06, 0x0e, 0x12, 0x14, 0x17}},
        {0xe1, 14, {0xd0, 0x00, 0x02, 0x07, 0x0a, 0x28, 0x31,
                   0x54, 0x47, 0x0e, 0x1c, 0x17, 0x1b, 0x1e}},
        {0x20, 0, {0}},                       /* JD9853A INVOFF */
    };
    for (size_t i = 0; i < ARRAY_SIZE(sequence); i++) {
        ret = st7789v_transmit(dev, sequence[i].cmd,
                              (uint8_t *)sequence[i].data, sequence[i].len);
        if (ret < 0) {
            LOG_ERR("Panel init cmd %02x failed: %d", sequence[i].cmd, ret);
            return ret;
        }
        if (sequence[i].cmd == 0x3a) { k_sleep(K_MSEC(10)); }
    }
    k_sleep(K_MSEC(120));
    ret = st7789v_clear_gram(dev);
    if (ret < 0) { return ret; }
    ret = st7789v_blanking_off(dev);
    k_sleep(K_MSEC(120));
    return ret;
}

static int st7789v_init(const struct device *dev)
{
	const struct st7789v_config *config = dev->config;
	int ret;

	if (!device_is_ready(config->mipi_dbi)) {
		LOG_ERR("MIPI DBI device not ready");
		return -ENODEV;
	}

	k_sleep(K_TIMEOUT_ABS_MS(config->ready_time_ms));

	ret = st7789v_reset_display(dev);
	if (ret < 0) {
		LOG_ERR("Failed to reset display (%d)", ret);
		return ret;
	}

	ret = st7789v_blanking_on(dev);
	if (ret < 0) {
		LOG_ERR("Failed to turn blanking on (%d)", ret);
		return ret;
	}

	ret = st7789v_lcd_init(dev);
	if (ret < 0) {
		LOG_ERR("Failed to init display (%d)", ret);
		return ret;
	}

	LOG_INF("JD9853A ready: %ux%u, offset 0/34, SPI %u Hz, native RGB565 -> MSB",
            config->width, config->height, config->dbi_config.config.frequency);
    return ret;
}

#ifdef CONFIG_PM_DEVICE
static int st7789v_pm_action(const struct device *dev,
			     enum pm_device_action action)
{
	int ret;

	switch (action) {
	case PM_DEVICE_ACTION_RESUME:
		ret = st7789v_exit_sleep(dev);
		break;
	case PM_DEVICE_ACTION_SUSPEND:
		ret = st7789v_transmit(dev, ST7789V_CMD_SLEEP_IN, NULL, 0);
		break;
	default:
		ret = -ENOTSUP;
		break;
	}

	return ret;
}
#endif /* CONFIG_PM_DEVICE */

static DEVICE_API(display, st7789v_api) = {
	.blanking_on = st7789v_blanking_on,
	.blanking_off = st7789v_blanking_off,
	.write = st7789v_write,
	.get_capabilities = st7789v_get_capabilities,
	.set_pixel_format = st7789v_set_pixel_format,
	.set_orientation = st7789v_set_orientation,
};

#define ST7789V_WORD_SIZE(inst)								\
	((DT_INST_STRING_UPPER_TOKEN(inst, mipi_mode) == MIPI_DBI_MODE_SPI_4WIRE) ?     \
	SPI_WORD_SET(8) : SPI_WORD_SET(9))
#define ST7789V_INIT(inst)								\
	static const struct st7789v_config st7789v_config_ ## inst = {			\
		.mipi_dbi = DEVICE_DT_GET(DT_INST_PARENT(inst)),                        \
		.dbi_config = MIPI_DBI_CONFIG_DT_INST(inst,                             \
						      ST7789V_WORD_SIZE(inst) |         \
						      SPI_OP_MODE_MASTER, 0),           \
		.vcom = DT_INST_PROP(inst, vcom),					\
		.gctrl = DT_INST_PROP(inst, gctrl),					\
		.vdv_vrh_enable = (DT_INST_NODE_HAS_PROP(inst, vrhs)			\
					&& DT_INST_NODE_HAS_PROP(inst, vdvs)),		\
		.vrh_value = DT_INST_PROP_OR(inst, vrhs, 0),				\
		.vdv_value = DT_INST_PROP_OR(inst, vdvs, 0),				\
		.mdac = DT_INST_PROP(inst, mdac),					\
		.gamma = DT_INST_PROP(inst, gamma),					\
		.colmod = DT_INST_PROP(inst, colmod),					\
		.lcm = DT_INST_PROP(inst, lcm),						\
		.inversion_on = !DT_INST_PROP(inst, inversion_off),			\
		.porch_param = DT_INST_PROP(inst, porch_param),				\
		.cmd2en_param = DT_INST_PROP(inst, cmd2en_param),			\
		.pwctrl1_param = DT_INST_PROP(inst, pwctrl1_param),			\
		.pvgam_param = DT_INST_PROP(inst, pvgam_param),				\
		.nvgam_param = DT_INST_PROP(inst, nvgam_param),				\
		.ram_param = DT_INST_PROP(inst, ram_param),				\
		.rgb_param = DT_INST_PROP(inst, rgb_param),				\
		.width = DT_INST_PROP(inst, width),					\
		.height = DT_INST_PROP(inst, height),					\
		.ready_time_ms = DT_INST_PROP(inst, ready_time_ms),			\
	};										\
											\
	static struct st7789v_data st7789v_data_ ## inst = {				\
		.x_offset = DT_INST_PROP(inst, x_offset),				\
		.y_offset = DT_INST_PROP(inst, y_offset),				\
		.orientation = DISPLAY_ORIENTATION_NORMAL,				\
	};										\
											\
	PM_DEVICE_DT_INST_DEFINE(inst, st7789v_pm_action);				\
											\
	DEVICE_DT_INST_DEFINE(inst, &st7789v_init, PM_DEVICE_DT_INST_GET(inst),		\
			&st7789v_data_ ## inst, &st7789v_config_ ## inst,		\
			POST_KERNEL, CONFIG_DISPLAY_INIT_PRIORITY,			\
			&st7789v_api);

DT_INST_FOREACH_STATUS_OKAY(ST7789V_INIT)
