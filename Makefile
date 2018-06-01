static_files = $(wildcard muchopper/web/static/css/*/*.css) $(wildcard muchopper/web/static/css/*.css)
compressed_files = $(addsuffix .gz,$(static_files))

compress: $(compressed_files)

$(compressed_files): %.gz: %
	gzip -9k "$<"
