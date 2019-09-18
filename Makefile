static_files = $(wildcard muchopper/web/static/css/*/*.css) $(wildcard muchopper/web/static/css/*.css muchopper/web/static/js/*.js)
compressed_files = $(addsuffix .gz,$(static_files))

compress: $(compressed_files)

$(compressed_files): %.gz: %
	gzip -f9k "$<"
