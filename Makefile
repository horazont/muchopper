scss_files = $(wildcard muchopper/web/scss/*.scss)
generated_css_files = $(patsubst muchopper/web/scss/%.scss,muchopper/web/static/css/%.css,$(scss_files))
static_files = $(wildcard muchopper/web/static/css/*/*.css) $(wildcard muchopper/web/static/js/*.js) $(generated_css_files)
compressed_files = $(addsuffix .gz,$(static_files))

PYTHON3 ?= python3
SCSSC ?= $(PYTHON3) -m scss

compress: $(compressed_files)

$(compressed_files): %.gz: %
	gzip -f9k "$<"

$(generated_css_files): muchopper/web/static/css/%.css: muchopper/web/scss/%.scss
	$(SCSSC) < $? > $@
