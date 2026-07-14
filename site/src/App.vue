<script setup lang="ts">
import SiteNav from './components/SiteNav.vue'
import TheHero from './components/TheHero.vue'
import TheStats from './components/TheStats.vue'
import ThePains from './components/ThePains.vue'
import TheCaps from './components/TheCaps.vue'
import TheMethod from './components/TheMethod.vue'
import TheFalsified from './components/TheFalsified.vue'
import TheApply from './components/TheApply.vue'
import TheFaq from './components/TheFaq.vue'
import TheFooter from './components/TheFooter.vue'
import { useReveal } from './useReveal'

// 一个 observer 管全站的滚动入场
useReveal(() => document)
</script>

<template>
  <SiteNav />
  <main>
    <TheHero />
    <TheStats />
    <ThePains />
    <TheCaps />
    <TheMethod />
    <TheFalsified />
    <TheApply />
    <TheFaq />
  </main>
  <TheFooter />
</template>
