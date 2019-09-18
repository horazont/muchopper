{% macro closed_marker() %}
{{ '  ' }}<span class="closed-marker" title="This room requires a password or invitation.">🔒</span>
{% endmacro %}
{% macro nonanon_marker() %}
<div><abbr title="This room is not anonymous; other occupants may be able to see your address.">⏿ Non-pseudonymous</abbr></div>
{% endmacro %}

{% macro dummy_avatar(address, caller=None) %}
{% set text = caller() %}
<div class="dummy-avatar" style="background-color: rgba({{ address | ccg_rgb_triplet }}, 1.0);"><span data-avatar-content="{{ text[0] }}"/></div>
{% endmacro %}

{% macro avatar(has_avatar, address, caller=None) %}
{% if has_avatar %}
<div class="real-avatar" style="background-image: url('{{ url_for('avatar_v1', address=address) }}'); "></div>
{% else %}
{% set content = caller() %}
{% call dummy_avatar(address) %}{{ content }}{% endcall %}
{% endif %}
{% endmacro %}

{% macro room_name(muc, public_info, caller=None) -%}
{%- if public_info.name and public_info.name != muc.address.localpart %}{{ public_info.name }}{% else %}{{ muc.address }}{% endif -%}
{%- endmacro %}

{% macro room_label(muc, public_info, keywords=[]) -%}
{%- if public_info.name and public_info.description and public_info.name != public_info.description and public_info.name != muc.address.localpart -%}
<span class="name">{{ public_info.name | highlight(keywords) }}</span><span class="address-suffix"> ({{ muc.address | highlight(keywords) }})</span>
{%- else -%}
{%- if muc.address.localpart -%}
<span class="address"><span class="localpart">{{ muc.address.localpart | highlight(keywords) }}</span><span class="at">@</span><span class="domain">{{ muc.address.domain | highlight(keywords) }}</span></span>
{%- else -%}
{{ muc.address | highlight(keywords) }}
{%- endif -%}
{%- endif -%}
{%- endmacro %}

{% macro logs_url(url, caller=None) -%}
<div><a href="{{ url }}" rel="nofollow"><abbr title="View history of {{ caller() }} in your browser">📜 View logs</abbr></a></div>
{%- endmacro %}

{% macro join_url(url, caller=None) -%}
<div><a href="{{ url }}" rel="nofollow"><abbr title="Join {{ caller() }} in your browser">💬 Join using browser</abbr></a></div>
{%- endmacro%}

{% macro room_table(items, keywords=[], caller=None) %}
<ol class="roomlist">
    {% for muc, public_info, has_avatar in items %}
    {% set nusers = (muc.nusers_moving_average or muc.nusers) | round %}
    {% set descr = public_info.description or public_info.name or public_info.subject %}
    {% set show_descr = descr and descr != muc.address.localpart %}
    {% set show_lang = public_info.language %}
    <li class="roomcard">
        <div class="avatar">{%- call avatar(has_avatar, muc.address) %}{% call room_name(muc, public_info) %}{% endcall %}{% endcall -%}</div>
        <div class="main">
            <div class="avatar">{%- call avatar(has_avatar, muc.address) %}{% call room_name(muc, public_info) %}{% endcall %}{% endcall -%}</div>
            <div class="addr">{#- -#}
                <a href="xmpp:{{ muc.address }}?join">{{ room_label(muc, public_info, keywords) }}</a><a title="Copy address to clipboard" class="copy-to-clipboard" onclick="copy_to_clipboard(this); return false;" data-cliptext="{{ muc.address }}" href="#">📋</a>
            </div>
            {%- if show_descr -%}
            <div class="descr">
                <span class="descr">{{ descr | highlight(keywords) }}</span>
            </div>
            {%- endif -%}
            <ul class="inline">
            <li>{{ "%.0f" | format(nusers) }} user{{ 's' if nusers != 1 else '' }} online</li>{#- -#}
            {%- if show_lang %}
            <li>Primary language: {{ public_info.language | prettify_lang }}</li>
            {%- endif -%}
            </ul>
        </div>
        <div class="meta">
            {%- if not muc.is_open %}{{ closed_marker() }}{% endif -%}
            {%- if not muc.anonymity_mode or muc.anonymity_mode.value == "none" %}{{ nonanon_marker() }}{% endif -%}
            {%- if public_info.web_chat_url %}{% call join_url(public_info.web_chat_url) %}{% call room_name(muc, public_info) %}{% endcall %}{% endcall %}{% endif -%}
            {%- if public_info.http_logs_url %}{% call logs_url(public_info.http_logs_url) %}{% call room_name(muc, public_info) %}{% endcall %}{% endcall %}{% endif -%}
        </div>
        <div class="flush"/>
    </li>
    {% endfor %}
</ol>
{% endmacro %}
