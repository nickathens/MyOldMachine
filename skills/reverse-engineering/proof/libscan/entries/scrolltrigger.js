import gsap from 'gsap';
import { ScrollTrigger } from 'gsap/ScrollTrigger';

gsap.registerPlugin(ScrollTrigger);
gsap.to('.box', { x: 100, scrollTrigger: { trigger: '.box', scrub: true } });
